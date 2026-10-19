"""Refreshes catalog prices from Shopee and Lazada.

    python -m app.ingestion.prices.refresh --category gpu --limit 50
    python -m app.ingestion.prices.refresh --retailers shopee --only-missing --dry-run

For each product: search each retailer, keep listings that match (see
matching.py), drop price outliers, store every accepted listing as a
PriceObservation, then set Product.price_php to the median. Products with no
accepted listing keep their previous price.
"""

import argparse
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.ingestion.prices.base import Listing, PriceSource, PriceSourceError
from app.ingestion.prices.matching import aggregate_price, drop_outliers, match_score, representative_listing
from app.models.price_observation import PriceObservation, Retailer
from app.models.product import Category, PriceType, Product
from app.services.parts_service import to_full_dict

SEARCH_LIMIT = 20


def build_sources(names: list[str]) -> list[PriceSource]:
    from app.ingestion.prices.lazada import LazadaAffiliateSource
    from app.ingestion.prices.shopee import ShopeeAffiliateSource

    constructors = {Retailer.shopee: ShopeeAffiliateSource, Retailer.lazada: LazadaAffiliateSource}
    sources = []
    for name in names:
        try:
            sources.append(constructors[Retailer(name)]())
        except PriceSourceError as exc:
            print(f"Skipping {name}: {exc}")
    return sources


def search_query(product: dict) -> str:
    return f"{product['manufacturer']} {product['model']}"


def match_listings(product: dict, listings: list[Listing]) -> list[tuple[Listing, float]]:
    scored = [(listing, match_score(product, listing.title)) for listing in listings]
    accepted = [listing for listing, score in scored if score > 0]
    kept = {id(listing) for listing in drop_outliers(accepted)}
    return [(listing, score) for listing, score in scored if score > 0 and id(listing) in kept]


def refresh_product(db: Session, product: Product, sources: list[PriceSource], dry_run: bool, interval_s: float) -> int:
    """Returns the number of listings accepted for this product."""
    details = to_full_dict(db, product)
    matched: list[tuple[Listing, float]] = []
    for source in sources:
        try:
            listings = source.search(search_query(details), limit=SEARCH_LIMIT)
        except PriceSourceError as exc:
            print(f"  {source.retailer.value}: {exc}")
            continue
        finally:
            time.sleep(interval_s)
        matched.extend(match_listings(details, listings))

    price = aggregate_price([listing for listing, _ in matched])
    label = f"{product.manufacturer} {product.model}"
    if price is None:
        print(f"  no match  {label}")
        return 0

    print(f"  ₱{price:>10,.2f}  {label}  ({len(matched)} listings)")
    if dry_run:
        return len(matched)

    now = datetime.now(timezone.utc)
    for listing, score in matched:
        db.add(PriceObservation(
            product_id=product.id,
            retailer=listing.retailer,
            listing_id=listing.listing_id,
            shop_name=listing.shop_name,
            is_official_store=listing.is_official_store,
            title=listing.title,
            url=listing.url,
            price_php=listing.price_php,
            match_score=score,
            observed_at=now,
        ))
    product.price_php = price
    product.price_type = PriceType.observed
    link = representative_listing([listing for listing, _ in matched], price)
    if link is not None:
        product.url = link.url
    product.price_updated_at = now
    return len(matched)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retailers", default="shopee,lazada", help="comma-separated: shopee,lazada")
    parser.add_argument("--category", choices=[c.value for c in Category])
    parser.add_argument("--only-missing", action="store_true", help="only products with no price yet")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sources = build_sources(args.retailers.split(","))
    if not sources:
        raise SystemExit("No retailer is configured. Set the Shopee and/or Lazada credentials in .env.")

    interval_s = get_settings().price_request_interval_s
    db = SessionLocal()
    try:
        stmt = select(Product).order_by(Product.price_updated_at.asc().nulls_first())
        if args.category:
            stmt = stmt.where(Product.category == Category(args.category))
        if args.only_missing:
            stmt = stmt.where(Product.price_php.is_(None))
        if args.limit:
            stmt = stmt.limit(args.limit)

        products = db.scalars(stmt).all()
        priced = 0
        for product in products:
            if refresh_product(db, product, sources, args.dry_run, interval_s):
                priced += 1
                if not args.dry_run:
                    db.commit()
        print(f"Priced {priced} of {len(products)} products.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
