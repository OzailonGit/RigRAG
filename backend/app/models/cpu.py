import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Cpu(Base):
    __tablename__ = "cpus"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    socket: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cores: Mapped[int] = mapped_column(Integer, nullable=False)
    threads: Mapped[int] = mapped_column(Integer, nullable=False)
    base_clock_ghz: Mapped[float] = mapped_column(nullable=False)
    boost_clock_ghz: Mapped[float] = mapped_column(nullable=False)
    tdp_w: Mapped[int] = mapped_column(Integer, nullable=False)
    has_igpu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    architecture: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[str] = mapped_column(String(64), nullable=False)
