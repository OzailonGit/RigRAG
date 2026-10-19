import enum
import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String
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

    # Board-partner cards (ASUS, MSI, ...) are sold under the card maker's
    # name, so brand preferences ("NVIDIA", "AMD") filter on the chip vendor.
    chip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chip_vendor: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    vram_gb: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vram_type: Mapped[VramType] = mapped_column(Enum(VramType, name="vram_type"), nullable=False)
    tdp_w: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_psu_w: Mapped[int] = mapped_column(Integer, nullable=False)
    pcie_gen: Mapped[str] = mapped_column(String(16), nullable=False)
    length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    performance_tier: Mapped[PerformanceTier] = mapped_column(Enum(PerformanceTier, name="performance_tier"), nullable=False)
    # Rasterization benchmark scores per resolution, as % of the fastest card
    # tested (data/reference/benchmarks.json, app.ingestion.import_benchmarks).
    raster_1080p: Mapped[float | None] = mapped_column(Float, nullable=True)
    raster_1440p: Mapped[float | None] = mapped_column(Float, nullable=True)
    raster_4k: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True when the scores are a stand-in from the card's tier, not a benchmark.
    benchmark_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
