"""add_scan_tracking_columns_to_monitored_paths

Revision ID: 3a6a9581dcf1
Revises: a_unified_migration
Create Date: 2026-01-17 17:58:47.230391

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "3a6a9581dcf1"
down_revision: Union[str, None] = "a_unified_migration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add scan tracking columns to monitored_paths table for existing databases."""
    # Get connection and inspector to check existing columns
    conn = op.get_bind()
    inspector = inspect(conn)

    # Get existing columns in monitored_paths table
    existing_columns = {col["name"] for col in inspector.get_columns("monitored_paths")}

    # Define columns to add with their definitions
    columns_to_add = {
        "last_scan_at": sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        "last_scan_status": sa.Column(
            "last_scan_status",
            sa.Enum("SUCCESS", "FAILURE", "PENDING", name="scanstatus"),
            nullable=True,
        ),
        "last_scan_error_log": sa.Column("last_scan_error_log", sa.Text(), nullable=True),
        "created_at": sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    }

    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table("monitored_paths", schema=None) as batch_op:
        # Only add columns that don't already exist
        for column_name, column_def in columns_to_add.items():
            if column_name not in existing_columns:
                batch_op.add_column(column_def)


def downgrade() -> None:
    """Remove scan tracking columns from monitored_paths table."""
    # Get connection and inspector to check existing columns
    conn = op.get_bind()
    inspector = inspect(conn)

    # Get existing columns in monitored_paths table
    existing_columns = {col["name"] for col in inspector.get_columns("monitored_paths")}

    # Columns to remove
    columns_to_remove = [
        "updated_at",
        "created_at",
        "last_scan_error_log",
        "last_scan_status",
        "last_scan_at",
    ]

    with op.batch_alter_table("monitored_paths", schema=None) as batch_op:
        # Only drop columns that exist
        for column_name in columns_to_remove:
            if column_name in existing_columns:
                batch_op.drop_column(column_name)
