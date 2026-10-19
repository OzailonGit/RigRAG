"""allow unverified case clearances

Revision ID: e1f4a7c2b903
Revises: c5e2b81f4a90
Create Date: 2026-10-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1f4a7c2b903'
down_revision: Union[str, None] = 'c5e2b81f4a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('cases', 'max_gpu_length_mm', existing_type=sa.Integer(), nullable=True)
    op.alter_column('cases', 'max_cooler_height_mm', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM products WHERE id IN (SELECT product_id FROM cases WHERE max_gpu_length_mm IS NULL OR max_cooler_height_mm IS NULL)")
    op.alter_column('cases', 'max_cooler_height_mm', existing_type=sa.Integer(), nullable=False)
    op.alter_column('cases', 'max_gpu_length_mm', existing_type=sa.Integer(), nullable=False)
