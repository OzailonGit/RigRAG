import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.product import PerformanceTier


class VramType(str, enum.Enum):
    gddr6 = "GDDR6"
    gddr6x = "GDDR6X"
    gddr7 = "GDDR7"
    hbm = "HBM"


class Gpu(Base):
    __tablename__ = "gpus"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    vram_gb: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vram_type: Mapped[VramType] = mapped_column(Enum(VramType, name="vram_type"), nullable=False)
    tdp_w: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_psu_w: Mapped[int] = mapped_column(Integer, nullable=False)
    pcie_gen: Mapped[str] = mapped_column(String(16), nullable=False)
    length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    performance_tier: Mapped[PerformanceTier] = mapped_column(Enum(PerformanceTier, name="performance_tier"), nullable=False)
