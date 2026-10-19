import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Retailer(str, enum.Enum):
    shopee = "shopee"
    lazada = "lazada"


class PriceObservation(Base):
    """One retailer listing matched to a product at a point in time.

    Rows are append-only so price history survives refreshes. Product.price_php
    is derived from the most recent observations (see app.ingestion.prices.refresh).
    """

    __tablename__ = "price_observations"
    __table_args__ = (Index("ix_price_observations_product_observed", "product_id", "observed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)

    retailer: Mapped[Retailer] = mapped_column(Enum(Retailer, name="retailer"), nullable=False)
    listing_id: Mapped[str] = mapped_column(String(64), nullable=False)
    shop_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_official_store: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    price_php: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
