import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Category(str, enum.Enum):
    cpu = "cpu"
    gpu = "gpu"
    motherboard = "motherboard"
    ram = "ram"
    storage = "storage"
    psu = "psu"
    cooler = "cooler"
    case = "case"


class PriceType(str, enum.Enum):
    estimated = "estimated"
    observed = "observed"
    live = "live"


class PerformanceTier(str, enum.Enum):
    entry = "entry"
    mid = "mid"
    high = "high"
    enthusiast = "enthusiast"


class Product(Base):
    """Shared identity + pricing row for every PC part, regardless of category.

    Category-specific specs live in the sibling tables (Cpu, Gpu, ...) keyed by
    product_id, so recommendation/compatibility code can query "all products"
    generically while spec access stays typed.

    `price_php` is NULL for imported products that have no retailer price yet;
    those are listed but never recommended.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[Category] = mapped_column(Enum(Category, name="category"), nullable=False, index=True)
    manufacturer: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)

    icecat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    mpn: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gtins: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    price_php: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_type: Mapped[PriceType] = mapped_column(Enum(PriceType, name="price_type"), nullable=False, default=PriceType.estimated)
    price_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source: Mapped[str] = mapped_column(Text, nullable=False)
    performance_tier: Mapped[PerformanceTier | None] = mapped_column(Enum(PerformanceTier, name="performance_tier"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.category}:{self.manufacturer} {self.model}>"
