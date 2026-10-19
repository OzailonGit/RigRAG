"""dataset import key and assumed spec fields

Revision ID: c5e2b81f4a90
Revises: a3c91e5d7b42
Create Date: 2026-10-19 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c5e2b81f4a90'
down_revision: Union[str, None] = 'a3c91e5d7b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('dataset_key', sa.String(length=200), nullable=True))
    op.create_unique_constraint('uq_products_dataset_key', 'products', ['dataset_key'])
    op.add_column('products', sa.Column('assumed_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))


def downgrade() -> None:
    op.drop_column('products', 'assumed_fields')
    op.drop_constraint('uq_products_dataset_key', 'products', type_='unique')
    op.drop_column('products', 'dataset_key')
