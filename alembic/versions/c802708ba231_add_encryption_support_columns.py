"""Add encryption support columns

Revision ID: c802708ba231
Revises: 1801504f0d51
Create Date: 2026-01-31 12:18:53.906412

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c802708ba231"
down_revision: Union[str, None] = "1801504f0d51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add encryption columns to cold_storage_locations
    with op.batch_alter_table("cold_storage_locations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_encrypted", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("encryption_status", sa.String(length=10), nullable=False, server_default="none"))

    # Add is_encrypted column to file_inventory
    with op.batch_alter_table("file_inventory", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_encrypted", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    # Remove encryption columns from file_inventory
    with op.batch_alter_table("file_inventory", schema=None) as batch_op:
        batch_op.drop_column("is_encrypted")

    # Remove encryption columns from cold_storage_locations
    with op.batch_alter_table("cold_storage_locations", schema=None) as batch_op:
        batch_op.drop_column("encryption_status")
        batch_op.drop_column("is_encrypted")
