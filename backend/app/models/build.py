import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Build(Base):
    """A generated/saved build for a chat session."""

    __tablename__ = "builds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    request_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total_price_php: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildItem(Base):
    """Join table: which product filled which role in a given build."""

    __tablename__ = "build_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    build_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("builds.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)


class CompatibilityRuleLog(Base):
    """Audit trail of which compatibility rule fired for a given build, for debugging/eval."""

    __tablename__ = "compatibility_rules_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    build_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("builds.id", ondelete="CASCADE"), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    compatible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
