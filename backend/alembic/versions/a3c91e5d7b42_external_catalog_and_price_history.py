"""external catalog identifiers and price history

Revision ID: a3c91e5d7b42
Revises: f7a4698edfc7
Create Date: 2026-10-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a3c91e5d7b42'
down_revision: Union[str, None] = 'f7a4698edfc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('icecat_id', sa.BigInteger(), nullable=True))
    op.add_column('products', sa.Column('mpn', sa.String(length=128), nullable=True))
    op.add_column('products', sa.Column('gtins', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.create_unique_constraint('uq_products_icecat_id', 'products', ['icecat_id'])
    op.create_index(op.f('ix_products_mpn'), 'products', ['mpn'], unique=False)
    op.alter_column('products', 'price_php', existing_type=sa.Numeric(precision=12, scale=2), nullable=True)

    op.add_column('gpus', sa.Column('chip', sa.String(length=64), nullable=True))
    op.add_column('gpus', sa.Column('chip_vendor', sa.String(length=16), nullable=True))
    op.create_index(op.f('ix_gpus_chip_vendor'), 'gpus', ['chip_vendor'], unique=False)
    # Seed GPUs are chip-level rows (manufacturer "NVIDIA", model "RTX 4060").
    op.execute(
        "UPDATE gpus SET chip_vendor = products.manufacturer, chip = products.model "
        "FROM products WHERE products.id = gpus.product_id"
    )

    op.alter_column('storage_devices', 'read_mb_s', existing_type=sa.Integer(), nullable=True)
    op.alter_column('storage_devices', 'write_mb_s', existing_type=sa.Integer(), nullable=True)
    op.alter_column('ram_kits', 'cas_latency', existing_type=sa.Integer(), nullable=True)
    op.alter_column('ram_kits', 'voltage', existing_type=sa.Float(), nullable=True)
    op.alter_column('psus', 'efficiency_rating', existing_type=sa.Enum(name='efficiency_rating'), nullable=True)
    op.alter_column('psus', 'atx_version', existing_type=sa.String(length=16), nullable=True)

    op.create_table('price_observations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('retailer', sa.Enum('shopee', 'lazada', name='retailer'), nullable=False),
    sa.Column('listing_id', sa.String(length=64), nullable=False),
    sa.Column('shop_name', sa.String(length=256), nullable=True),
    sa.Column('is_official_store', sa.Boolean(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('price_php', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('match_score', sa.Float(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_price_observations_product_observed', 'price_observations', ['product_id', 'observed_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_price_observations_product_observed', table_name='price_observations')
    op.drop_table('price_observations')
    sa.Enum(name='retailer').drop(op.get_bind(), checkfirst=True)

    op.execute("DELETE FROM products WHERE price_php IS NULL")
    op.alter_column('psus', 'atx_version', existing_type=sa.String(length=16), nullable=False)
    op.alter_column('psus', 'efficiency_rating', existing_type=sa.Enum(name='efficiency_rating'), nullable=False)
    op.alter_column('ram_kits', 'voltage', existing_type=sa.Float(), nullable=False)
    op.alter_column('ram_kits', 'cas_latency', existing_type=sa.Integer(), nullable=False)
    op.alter_column('storage_devices', 'write_mb_s', existing_type=sa.Integer(), nullable=False)
    op.alter_column('storage_devices', 'read_mb_s', existing_type=sa.Integer(), nullable=False)

    op.drop_index(op.f('ix_gpus_chip_vendor'), table_name='gpus')
    op.drop_column('gpus', 'chip_vendor')
    op.drop_column('gpus', 'chip')

    op.alter_column('products', 'price_php', existing_type=sa.Numeric(precision=12, scale=2), nullable=False)
    op.drop_index(op.f('ix_products_mpn'), table_name='products')
    op.drop_constraint('uq_products_icecat_id', 'products', type_='unique')
    op.drop_column('products', 'gtins')
    op.drop_column('products', 'mpn')
    op.drop_column('products', 'icecat_id')
