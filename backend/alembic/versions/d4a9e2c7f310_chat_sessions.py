"""chat sessions

Revision ID: d4a9e2c7f310
Revises: b8d3f0a6c217
Create Date: 2026-10-19 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd4a9e2c7f310'
down_revision: Union[str, None] = 'b8d3f0a6c217'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_sessions_updated_at', 'chat_sessions', ['updated_at'])


def downgrade() -> None:
    op.drop_index('ix_chat_sessions_updated_at', table_name='chat_sessions')
    op.drop_table('chat_sessions')
