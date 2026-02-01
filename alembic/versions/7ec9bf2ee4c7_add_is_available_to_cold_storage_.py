"""add_is_available_to_cold_storage_locations

Revision ID: 7ec9bf2ee4c7
Revises: c802708ba231
Create Date: 2026-01-31 23:08:14.041024

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ec9bf2ee4c7'
down_revision: Union[str, None] = 'c802708ba231'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cold_storage_locations', sa.Column('is_available', sa.Boolean(), nullable=False, server_default=sa.text('1')))


def downgrade() -> None:
    op.drop_column('cold_storage_locations', 'is_available')
