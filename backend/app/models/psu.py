import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class EfficiencyRating(str, enum.Enum):
    white = "80+ White"
    bronze = "80+ Bronze"
    silver = "80+ Silver"
    gold = "80+ Gold"
    platinum = "80+ Platinum"
    titanium = "80+ Titanium"


class Psu(Base):
    __tablename__ = "psus"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    wattage: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    efficiency_rating: Mapped[EfficiencyRating | None] = mapped_column(Enum(EfficiencyRating, name="efficiency_rating"), nullable=True)
    atx_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pcie_connectors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    supports_12vhpwr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
