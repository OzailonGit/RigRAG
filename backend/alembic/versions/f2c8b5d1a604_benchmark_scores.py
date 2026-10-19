"""benchmark scores for cpus and gpus

Revision ID: f2c8b5d1a604
Revises: d4a9e2c7f310
Create Date: 2026-10-19 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2c8b5d1a604'
down_revision: Union[str, None] = 'd4a9e2c7f310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cpus', sa.Column('gaming_score', sa.Float(), nullable=True))
    op.add_column('cpus', sa.Column('multithread_score', sa.Float(), nullable=True))
    op.add_column('cpus', sa.Column('benchmark_estimated', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('gpus', sa.Column('raster_1080p', sa.Float(), nullable=True))
    op.add_column('gpus', sa.Column('raster_1440p', sa.Float(), nullable=True))
    op.add_column('gpus', sa.Column('raster_4k', sa.Float(), nullable=True))
    op.add_column('gpus', sa.Column('benchmark_estimated', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    for column in ('benchmark_estimated', 'raster_4k', 'raster_1440p', 'raster_1080p'):
        op.drop_column('gpus', column)
    for column in ('benchmark_estimated', 'multithread_score', 'gaming_score'):
        op.drop_column('cpus', column)
