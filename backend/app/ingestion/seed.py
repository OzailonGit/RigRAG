"""Loads the curated seed dataset (data/seed/*.json) into the structured database.

First stage of the ingestion pipeline: curated rows become validated
Product and category spec rows. Embedding and indexing run separately in
app.ingestion.embed_and_index.
"""

import json
from pathlib import Path

from sqlalchemy import inspect

from app.database.base import Base
from app.database.session import SessionLocal, engine
from app.models.product import Category, PerformanceTier, PriceType, Product
from app.services.parts_service import SPEC_MODEL_BY_CATEGORY

SEED_DIR = Path(__file__).resolve().parents[3] / "data" / "seed"

FILE_TO_CATEGORY = {
    "cpus.json": Category.cpu,
    "gpus.json": Category.gpu,
    "motherboards.json": Category.motherboard,
    "ram.json": Category.ram,
    "storage.json": Category.storage,
    "psus.json": Category.psu,
    "coolers.json": Category.cooler,
    "cases.json": Category.case,
}

# performance_tier is intentionally not here: Product carries a general tier
# for every category, but Gpu also defines its own performance_tier column,
# so the value must flow through to spec_kwargs for GPUs.
PRODUCT_ONLY_FIELDS = {"manufacturer", "model", "price_php", "price_type", "source"}


def load_category(db, filename: str, category: Category) -> int:
    path = SEED_DIR / filename
    rows = json.loads(path.read_text(encoding="utf-8"))
    spec_model = SPEC_MODEL_BY_CATEGORY[category]

    count = 0
    for row in rows:
        product = Product(
            category=category,
            manufacturer=row["manufacturer"],
            model=row["model"],
            price_php=row["price_php"],
            price_type=PriceType(row["price_type"]),
            source=row["source"],
            performance_tier=PerformanceTier(row["performance_tier"]) if row.get("performance_tier") else None,
        )
        db.add(product)
        db.flush()  # populate product.id

        spec_columns = {c.key for c in inspect(spec_model).column_attrs}
        spec_kwargs = {
            k: v for k, v in row.items()
            if k in spec_columns and (k not in PRODUCT_ONLY_FIELDS or k == "performance_tier")
        }
        spec = spec_model(product_id=product.id, **spec_kwargs)
        db.add(spec)
        count += 1

    return count


def main() -> None:
    Base.metadata.create_all(bind=engine)  # convenience for local/dev; Alembic remains source of truth

    db = SessionLocal()
    try:
        existing = db.query(Product).count()
        if existing:
            print(f"Skipping seed: {existing} products already present.")
            return

        total = 0
        for filename, category in FILE_TO_CATEGORY.items():
            n = load_category(db, filename, category)
            print(f"Loaded {n} {category.value} products from {filename}")
            total += n

        db.commit()
        print(f"Seed complete: {total} products.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
