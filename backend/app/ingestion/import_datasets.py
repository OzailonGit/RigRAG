"""Imports the combined public datasets (data/combined/*.jsonl) into the catalog.

    python -m app.ingestion.datasets.build          # produce data/combined first
    python -m app.ingestion.import_datasets --dry-run
    python -m app.ingestion.import_datasets --categories cpu,gpu --priced-only

Rows are mapped by app.ingestion.normalization.datasets and checked by the
same validator as Icecat imports. Colour variants of one product collapse
into a single row at the lowest listed price.

Prices are estimates converted from the PCPartPicker listings in the
datasets: the French EUR price (newer) when there is one, otherwise the US
price. Both are adjusted to Philippine VAT. Re-running the import updates
specs and estimated prices in place and never overwrites a price that the
Shopee/Lazada refresh has observed.
"""

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.ingestion.normalization import datasets as mapping
from app.ingestion.validation.specs import validate_spec
from app.models.product import Category, PerformanceTier, PriceType, Product
from app.services.parts_service import SPEC_MODEL_BY_CATEGORY

COMBINED_DIR = Path(__file__).resolve().parents[3] / "data" / "combined"

# Approximate exchange rates; pass --eur-php / --usd-php to use current ones.
DEFAULT_EUR_PHP = 66.0
DEFAULT_USD_PHP = 57.0
FR_VAT = Decimal("1.20")  # PCPartPicker FR prices include French VAT
PH_VAT = Decimal("1.12")  # US prices exclude sales tax
MAX_MSRP_MARKUP = Decimal("1.5")

MAPPERS = {
    Category.cpu: mapping.map_cpu,
    Category.motherboard: mapping.map_motherboard,
    Category.ram: mapping.map_ram,
    Category.storage: mapping.map_storage,
    Category.psu: mapping.map_psu,
    Category.cooler: mapping.map_cooler,
    Category.case: mapping.map_case,
}


@dataclass
class Candidate:
    mapped: mapping.MappedRow
    price_php: Decimal | None
    price_note: str | None
    sources: str


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    priced: int = 0
    skipped: Counter = field(default_factory=Counter)

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def dataset_key(mapped: mapping.MappedRow) -> str:
    return f"{mapped.category.value}:{_slug(mapped.manufacturer)}:{_slug(mapped.model)}"[:200]


def estimate_price_php(row: dict, eur_php: float, usd_php: float) -> tuple[Decimal | None, str | None]:
    """Converts a listing price to pesos, rounded to the nearest ₱10.

    A listing far above launch MSRP is usually leftover stock of a
    discontinued part, so it is dropped rather than used as an estimate.
    """
    if row.get("price_eur"):
        php = Decimal(str(row["price_eur"])) * Decimal(str(eur_php)) / FR_VAT * PH_VAT
        note = "EUR listing (PCPartPicker FR, 2026-05)"
    elif row.get("price_usd"):
        php = Decimal(str(row["price_usd"])) * Decimal(str(usd_php)) * PH_VAT
        note = "USD listing (PCPartPicker US, 2025-07)"
    else:
        return None, None
    if row.get("msrp_usd"):
        msrp_php = Decimal(str(row["msrp_usd"])) * Decimal(str(usd_php)) * PH_VAT
        if php > MAX_MSRP_MARKUP * msrp_php:
            return None, None
    return (php / 10).quantize(Decimal("1")) * 10, note


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def collect(category: Category, directory: Path, args, report: ImportReport, chip_table) -> dict[str, Candidate]:
    """Maps and validates every row of one category, keeping the cheapest row per product."""
    path = directory / f"{category.value}.jsonl"
    if not path.exists():
        report.skip(f"{category.value}: {path.name} not found")
        return {}

    candidates: dict[str, Candidate] = {}
    for row in _read_jsonl(path):
        try:
            mapped = mapping.map_gpu(row, chip_table) if category is Category.gpu else MAPPERS[category](row)
        except mapping.MappingError as exc:
            report.skip(f"{category.value}: {exc}")
            continue
        problems = validate_spec(category, mapped.spec)
        if problems:
            report.skip(f"{category.value}: {problems[0]}")
            continue
        price, note = estimate_price_php(row, args.eur_php, args.usd_php)
        if price is None and args.priced_only:
            report.skip(f"{category.value}: no price")
            continue

        key = dataset_key(mapped)
        current = candidates.get(key)
        if current is None or (price is not None and (current.price_php is None or price < current.price_php)):
            candidates[key] = Candidate(mapped, price, note, row.get("sources") or "")
    return candidates


def _source_text(candidate: Candidate) -> str:
    parts = [f"Combined public datasets ({candidate.sources})"]
    if candidate.mapped.spec_source:
        parts.append(f"clearance and socket specs from {candidate.mapped.spec_source}")
    if candidate.mapped.unverified:
        parts.append(f"not listed by any source: {', '.join(candidate.mapped.unverified)}")
    if candidate.price_note:
        parts.append(f"price estimated from {candidate.price_note}")
    return "; ".join(parts)


def upsert(db: Session, category: Category, candidates: dict[str, Candidate], report: ImportReport) -> None:
    spec_model = SPEC_MODEL_BY_CATEGORY[category]
    existing = {p.dataset_key: p for p in db.scalars(
        select(Product).where(Product.category == category, Product.dataset_key.is_not(None))
    )}
    # Seed and Icecat rows already describe these parts with curated or
    # observed data, so the dataset copy is skipped.
    curated = {
        (_slug(p.manufacturer), _slug(p.model))
        for p in db.scalars(select(Product).where(Product.category == category, Product.dataset_key.is_(None)))
    }
    now = datetime.now(timezone.utc)

    for key, candidate in candidates.items():
        mapped = candidate.mapped
        if (_slug(mapped.manufacturer), _slug(mapped.model)) in curated:
            report.skip(f"{category.value}: already in catalog")
            continue
        tier = PerformanceTier(mapped.performance_tier) if mapped.performance_tier else None
        if candidate.price_php is not None:
            report.priced += 1

        product = existing.get(key)
        if product is None:
            product = Product(
                category=category,
                manufacturer=mapped.manufacturer[:128],
                model=mapped.model[:256],
                dataset_key=key,
                price_php=candidate.price_php,
                price_type=PriceType.estimated,
                price_updated_at=now,
                source=_source_text(candidate),
                performance_tier=tier,
                assumed_fields=mapped.assumed,
            )
            db.add(product)
            db.flush()
            db.add(spec_model(product_id=product.id, **mapped.spec))
            report.created += 1
            continue

        product.manufacturer = mapped.manufacturer[:128]
        product.model = mapped.model[:256]
        product.performance_tier = tier
        product.assumed_fields = mapped.assumed
        if product.price_type is PriceType.estimated:
            product.price_php = candidate.price_php
            product.price_updated_at = now
            product.source = _source_text(candidate)
        spec = db.get(spec_model, product.id)
        if spec is None:
            db.add(spec_model(product_id=product.id, **mapped.spec))
        else:
            for name, value in mapped.spec.items():
                setattr(spec, name, value)
        report.updated += 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=COMBINED_DIR, help="folder with the combined .jsonl files")
    parser.add_argument("--categories", help="comma-separated categories, e.g. cpu,gpu (default: all)")
    parser.add_argument("--priced-only", action="store_true", help="skip rows with no listing price")
    parser.add_argument("--eur-php", type=float, default=DEFAULT_EUR_PHP, help="pesos per euro")
    parser.add_argument("--usd-php", type=float, default=DEFAULT_USD_PHP, help="pesos per US dollar")
    parser.add_argument("--dry-run", action="store_true", help="map and validate without writing")
    args = parser.parse_args(argv)

    categories = [Category(c) for c in args.categories.split(",")] if args.categories else list(Category)
    chip_path = args.dir / "gpu_chip.jsonl"
    chip_table = mapping.load_chip_table(_read_jsonl(chip_path)) if chip_path.exists() else {}

    report = ImportReport()
    db = SessionLocal()
    try:
        for category in categories:
            candidates = collect(category, args.dir, args, report, chip_table)
            if args.dry_run:
                report.created += len(candidates)
                report.priced += sum(1 for c in candidates.values() if c.price_php is not None)
            else:
                upsert(db, category, candidates, report)
                db.commit()
            print(f"  {category.value}: {len(candidates)} products")
    finally:
        db.close()

    verb = "Would import" if args.dry_run else "Imported"
    print(f"{verb} {report.created} new, updated {report.updated} ({report.priced} with an estimated price), "
          f"skipped {sum(report.skipped.values())} rows.")
    for reason, n in report.skipped.most_common(25):
        print(f"  {n:>6}  {reason}")


if __name__ == "__main__":
    main()
