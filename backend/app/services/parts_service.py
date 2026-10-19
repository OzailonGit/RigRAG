import enum
import uuid

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.cooler import CpuCooler
from app.models.cpu import Cpu
from app.models.gpu import Gpu
from app.models.motherboard import Motherboard
from app.models.product import Category, Product
from app.models.psu import Psu
from app.models.ram import RamKit
from app.models.storage import StorageDevice

SPEC_MODEL_BY_CATEGORY = {
    Category.cpu: Cpu,
    Category.gpu: Gpu,
    Category.motherboard: Motherboard,
    Category.ram: RamKit,
    Category.storage: StorageDevice,
    Category.psu: Psu,
    Category.cooler: CpuCooler,
    Category.case: Case,
}


def _row_to_dict(row) -> dict:
    """Flatten an ORM row to a dict, unwrapping enum columns to their plain value.

    str-mixin enums (e.g. `class RamType(str, enum.Enum)`) render as
    "RamType.ddr5" under str()/f-strings on Python 3.11+, since CPython
    changed Enum.__str__/__format__ to stop deferring to the mixin. Every
    consumer of this dict (compatibility messages, the recommendation engine,
    LLM context) wants the plain value ("DDR5"), so unwrap once here.
    """
    result = {}
    for c in inspect(row).mapper.column_attrs:
        value = getattr(row, c.key)
        result[c.key] = value.value if isinstance(value, enum.Enum) else value
    return result


def to_full_dict(db: Session, product: Product) -> dict:
    """Merge the shared Product row with its category-specific spec row into one flat dict."""
    data = _row_to_dict(product)
    spec_model = SPEC_MODEL_BY_CATEGORY[product.category]
    spec = db.get(spec_model, product.id)
    if spec is not None:
        spec_data = _row_to_dict(spec)
        spec_data.pop("product_id", None)
        data.update(spec_data)
    return data


def list_parts(
    db: Session,
    category: Category | None = None,
    max_price_php: float | None = None,
    manufacturer: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    stmt = select(Product)
    if category is not None:
        stmt = stmt.where(Product.category == category)
    if max_price_php is not None:
        stmt = stmt.where(Product.price_php <= max_price_php)
    if manufacturer is not None:
        stmt = stmt.where(Product.manufacturer.ilike(manufacturer))
    stmt = stmt.order_by(Product.category, Product.price_php).offset(offset).limit(limit)

    products = db.scalars(stmt).all()
    return [to_full_dict(db, p) for p in products]


def get_part(db: Session, product_id: uuid.UUID) -> dict | None:
    product = db.get(Product, product_id)
    if product is None:
        return None
    return to_full_dict(db, product)
