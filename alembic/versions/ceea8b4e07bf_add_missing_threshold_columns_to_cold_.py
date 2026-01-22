"""add missing threshold columns to cold_storage_locations

Revision ID: ceea8b4e07bf
Revises: 563e30c9d3e0
Create Date: 2026-01-22 20:23:03.317342

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ceea8b4e07bf'
down_revision: Union[str, None] = '563e30c9d3e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('cold_storage_locations')]

    if 'caution_threshold_percent' not in columns:
        op.add_column('cold_storage_locations', sa.Column('caution_threshold_percent', sa.Integer(), nullable=False, server_default='20'))

    if 'critical_threshold_percent' not in columns:
        op.add_column('cold_storage_locations', sa.Column('critical_threshold_percent', sa.Integer(), nullable=False, server_default='10'))


def downgrade() -> None:
    op.drop_column('cold_storage_locations', 'critical_threshold_percent')
    op.drop_column('cold_storage_locations', 'caution_threshold_percent')
