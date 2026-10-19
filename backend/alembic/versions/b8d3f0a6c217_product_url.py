"""product link

Revision ID: b8d3f0a6c217
Revises: e1f4a7c2b903
Create Date: 2026-10-19 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b8d3f0a6c217'
down_revision: Union[str, None] = 'e1f4a7c2b903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('url', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'url')
