import uuid

from sqlalchemy import Enum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.motherboard import RamType


class RamKit(Base):
    __tablename__ = "ram_kits"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    capacity_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    sticks: Mapped[int] = mapped_column(Integer, nullable=False)
    ddr_gen: Mapped[RamType] = mapped_column(Enum(RamType, name="ram_type"), nullable=False)
    speed_mhz: Mapped[int] = mapped_column(Integer, nullable=False)
    cas_latency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voltage: Mapped[float | None] = mapped_column(nullable=True)
