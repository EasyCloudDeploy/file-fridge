"""add_instance_url_and_name

Adds instance_url and instance_name columns to instance_metadata table
for UI-based configuration of remote connections.

Revision ID: add_instance_url_and_name
Revises: add_missing_instance_metadata
Create Date: 2026-01-28 19:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_instance_url_and_name"
down_revision: Union[str, None] = "add_missing_instance_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add instance_url and instance_name columns to instance_metadata table if they don't exist."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if instance_metadata table exists
    if "instance_metadata" not in inspector.get_table_names():
        # Table will be created by database.py init_db(), skip this migration
        return

    existing_columns = {col["name"] for col in inspector.get_columns("instance_metadata")}

    columns_to_add = [
        (
            "instance_url",
            sa.Column("instance_url", sa.String(), nullable=True),
        ),
        (
            "instance_name",
            sa.Column("instance_name", sa.String(), nullable=True),
        ),
    ]

    for column_name, column_def in columns_to_add:
        if column_name not in existing_columns:
            with op.batch_alter_table("instance_metadata", schema=None) as batch_op:
                batch_op.add_column(column_def)


def downgrade() -> None:
    """Remove the columns added by this migration."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "instance_metadata" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("instance_metadata")}

    columns_to_remove = ["instance_url", "instance_name"]

    for column_name in columns_to_remove:
        if column_name in existing_columns:
            with op.batch_alter_table("instance_metadata", schema=None) as batch_op:
                batch_op.drop_column(column_name)
