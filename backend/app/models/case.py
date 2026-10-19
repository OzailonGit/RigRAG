import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Case(Base):
    __tablename__ = "cases"

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    form_factor_support: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # NULL when no source lists the clearance; the rules then warn instead of checking.
    max_gpu_length_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cooler_height_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    psu_form_factor: Mapped[str] = mapped_column(String(16), nullable=False, default="ATX")
    radiator_support: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fan_mounts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
