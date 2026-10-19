import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class FormFactor(str, enum.Enum):
    atx = "ATX"
    micro_atx = "Micro-ATX"
    mini_itx = "Mini-ITX"
    e_atx = "E-ATX"


class RamType(str, enum.Enum):
    ddr4 = "DDR4"
    ddr5 = "DDR5"


class Motherboard(Base):
    __tablename__ = "motherboards"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    socket: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chipset: Mapped[str] = mapped_column(String(64), nullable=False)
    form_factor: Mapped[FormFactor] = mapped_column(Enum(FormFactor, name="form_factor"), nullable=False, index=True)
    ram_type: Mapped[RamType] = mapped_column(Enum(RamType, name="ram_type"), nullable=False)
    max_ram_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    m2_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    sata_ports: Mapped[int] = mapped_column(Integer, nullable=False)
    wifi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bluetooth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pcie_slots: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    bios_flash_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
