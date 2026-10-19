import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class CoolerType(str, enum.Enum):
    air = "air"
    aio = "aio"


class CpuCooler(Base):
    __tablename__ = "cpu_coolers"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    type: Mapped[CoolerType] = mapped_column(Enum(CoolerType, name="cooler_type"), nullable=False)
    socket_support: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    height_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    radiator_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tdp_rating_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
