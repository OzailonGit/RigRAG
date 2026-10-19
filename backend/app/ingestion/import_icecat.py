"""Imports Open Icecat datasheets into the catalog.

Single products (works with the demo account):
    python -m app.ingestion.import_icecat --product ASUS:90YV0JC0-M0NA00 --gtin 4711387223734

Whole categories from the index file (needs a registered account):
    python -m app.ingestion.import_icecat --index --categories gpu,motherboard --country PH --limit 500

Imported products have no price until app.ingestion.prices.refresh finds
retailer listings for them, so they are listed but never recommended yet.
Re-running an import updates specs in place and leaves prices untouched.
"""

import argparse
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.ingestion.normalization.icecat import ICECAT_IDS_BY_CATEGORY, MappedProduct, MappingError, map_icecat_product
from app.ingestion.normalization.overrides import apply_overrides
from app.ingestion.parsers.icecat_index import iter_index
from app.ingestion.sources.icecat import IcecatClient, LookupResult, LookupStatus
from app.ingestion.validation.specs import validate_spec
from app.models.product import Category, PerformanceTier, PriceType, Product
from app.services.parts_service import SPEC_MODEL_BY_CATEGORY

COMMIT_EVERY = 100


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: Counter = field(default_factory=Counter)

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1


def upsert_product(db: Session, mapped: MappedProduct) -> str:
    """Creates or updates the Product + spec rows for one datasheet, matched
    on icecat_id. Returns "created" or "updated"."""
    spec_model = SPEC_MODEL_BY_CATEGORY[mapped.category]
    tier = mapped.product.get("performance_tier")
    product_fields = {k: v for k, v in mapped.product.items() if k != "performance_tier"}

    product = db.scalar(select(Product).where(Product.icecat_id == mapped.product["icecat_id"]))
    if product is None:
        product = Product(
            category=mapped.category,
            price_php=None,
            price_type=PriceType.estimated,
            performance_tier=PerformanceTier(tier) if tier else None,
            **product_fields,
        )
        db.add(product)
        db.flush()
        db.add(spec_model(product_id=product.id, **mapped.spec))
        return "created"

    for key, value in product_fields.items():
        setattr(product, key, value)
    product.performance_tier = PerformanceTier(tier) if tier else None
    spec = db.get(spec_model, product.id)
    if spec is None:
        db.add(spec_model(product_id=product.id, **mapped.spec))
    else:
        for key, value in mapped.spec.items():
            setattr(spec, key, value)
    return "updated"


def process_lookup(db: Session, result: LookupResult, report: ImportReport, dry_run: bool) -> None:
    if result.status is not LookupStatus.ok:
        report.skip(f"lookup {result.status.value}")
        return
    try:
        mapped = map_icecat_product(result.data)
    except MappingError as exc:
        report.skip(f"mapping: {exc}")
        return

    apply_overrides(mapped.product.get("mpn"), mapped.spec)
    problems = validate_spec(mapped.category, mapped.spec)
    if problems:
        for problem in problems:
            report.skip(f"{mapped.category.value}: {problem}")
        return

    if dry_run:
        report.created += 1
        return
    outcome = upsert_product(db, mapped)
    if outcome == "created":
        report.created += 1
    else:
        report.updated += 1


def _index_lookups(client: IcecatClient, args) -> Iterator[LookupResult]:
    categories = [Category(c) for c in args.categories.split(",")] if args.categories else list(ICECAT_IDS_BY_CATEGORY)
    category_ids = [cid for c in categories for cid in ICECAT_IDS_BY_CATEGORY.get(c, [])]
    index_path = Path(args.index_file) if args.index_file else client.download_index()

    count = 0
    for entry in iter_index(index_path, category_ids=category_ids, country=args.country):
        if not entry.on_market and not args.include_off_market:
            continue
        yield client.get_product(icecat_id=entry.icecat_id)
        count += 1
        if args.limit and count >= args.limit:
            return


def _explicit_lookups(client: IcecatClient, args) -> Iterator[LookupResult]:
    for gtin in args.gtin or []:
        yield client.get_product(gtin=gtin)
    for spec in args.product or []:
        brand, _, code = spec.partition(":")
        if not code:
            raise SystemExit(f"--product expects BRAND:CODE, got {spec!r}")
        yield client.get_product(brand=brand, product_code=code)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", action="store_true", help="enumerate products from the Open Icecat index")
    parser.add_argument("--index-file", help="use an already-downloaded index file (.xml or .xml.gz)")
    parser.add_argument("--categories", help="comma-separated RigRAG categories, e.g. gpu,motherboard")
    parser.add_argument("--country", help="keep only products marketed in this country code, e.g. PH")
    parser.add_argument("--include-off-market", action="store_true")
    parser.add_argument("--limit", type=int, help="stop after this many index entries")
    parser.add_argument("--gtin", action="append", help="import one product by EAN/UPC (repeatable)")
    parser.add_argument("--product", action="append", help="import one product by BRAND:CODE (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="map and validate without writing")
    args = parser.parse_args(argv)

    if not (args.index or args.index_file or args.gtin or args.product):
        parser.error("nothing to import: pass --index, --index-file, --gtin, or --product")

    client = IcecatClient()
    lookups = _index_lookups(client, args) if (args.index or args.index_file) else _explicit_lookups(client, args)

    report = ImportReport()
    db = SessionLocal()
    try:
        for i, result in enumerate(lookups, start=1):
            process_lookup(db, result, report, args.dry_run)
            if not args.dry_run and i % COMMIT_EVERY == 0:
                db.commit()
                print(f"  ...{i} processed")
        if not args.dry_run:
            db.commit()
    finally:
        db.close()

    verb = "Would import" if args.dry_run else "Imported"
    print(f"{verb} {report.created} new, updated {report.updated}, skipped {sum(report.skipped.values())}.")
    for reason, n in report.skipped.most_common(20):
        print(f"  {n:>5}  {reason}")


if __name__ == "__main__":
    main()
