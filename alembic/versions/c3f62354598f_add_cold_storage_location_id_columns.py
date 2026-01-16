"""Add cold_storage_location_id columns to file_records and file_inventory

Revision ID: c3f62354598f
Revises: ee17616b5a90
Create Date: 2026-01-15 22:50:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f62354598f"
down_revision: Union[str, None] = "ee17616b5a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table (SQLite compatible)."""
    bind = op.get_bind()
    result = bind.execute(sa.text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result]
    return column_name in columns


def index_exists(index_name: str) -> bool:
    """Check if an index exists (SQLite compatible)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:name"),
        {"name": index_name}
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # Add cold_storage_location_id to file_records table
    if not column_exists("file_records", "cold_storage_location_id"):
        op.add_column(
            "file_records",
            sa.Column("cold_storage_location_id", sa.Integer(), nullable=True)
        )

    if not index_exists("idx_file_records_cold_storage_location_id"):
        op.create_index(
            "idx_file_records_cold_storage_location_id",
            "file_records",
            ["cold_storage_location_id"],
            unique=False
        )

    # Add cold_storage_location_id to file_inventory table
    if not column_exists("file_inventory", "cold_storage_location_id"):
        op.add_column(
            "file_inventory",
            sa.Column("cold_storage_location_id", sa.Integer(), nullable=True)
        )

    if not index_exists("idx_file_inventory_cold_storage_location_id"):
        op.create_index(
            "idx_file_inventory_cold_storage_location_id",
            "file_inventory",
            ["cold_storage_location_id"],
            unique=False
        )


def downgrade() -> None:
    # Remove from file_inventory
    if index_exists("idx_file_inventory_cold_storage_location_id"):
        op.drop_index("idx_file_inventory_cold_storage_location_id", table_name="file_inventory")

    if column_exists("file_inventory", "cold_storage_location_id"):
        op.drop_column("file_inventory", "cold_storage_location_id")

    # Remove from file_records
    if index_exists("idx_file_records_cold_storage_location_id"):
        op.drop_index("idx_file_records_cold_storage_location_id", table_name="file_records")

    if column_exists("file_records", "cold_storage_location_id"):
        op.drop_column("file_records", "cold_storage_location_id")
