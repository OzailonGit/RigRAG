import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class StorageType(str, enum.Enum):
    ssd_nvme = "SSD_NVME"
    ssd_sata = "SSD_SATA"
    hdd = "HDD"


class StorageDevice(Base):
    __tablename__ = "storage_devices"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    type: Mapped[StorageType] = mapped_column(Enum(StorageType, name="storage_type"), nullable=False, index=True)
    capacity_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    interface: Mapped[str] = mapped_column(String(32), nullable=False)
    pcie_gen: Mapped[str | None] = mapped_column(String(16), nullable=True)
    read_mb_s: Mapped[int] = mapped_column(Integer, nullable=False)
    write_mb_s: Mapped[int] = mapped_column(Integer, nullable=False)
