"""add_missing_instance_metadata_columns

Idempotent migration to ensure instance_metadata table has all required
cryptographic key columns. This migration is needed to fix customer
installations where the database schema is missing these columns due to
failed or incomplete migrations.

Revision ID: add_missing_instance_metadata
Revises: 1c10588157df
Create Date: 2026-01-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_missing_instance_metadata"
down_revision: Union[str, None] = "1c10588157df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing columns to instance_metadata table if they don't exist."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_columns = {col["name"] for col in inspector.get_columns("instance_metadata")}

    columns_to_add = [
        ("ed25519_public_key", sa.Column("ed25519_public_key", sa.Text(), nullable=True)),
        (
            "ed25519_private_key_encrypted",
            sa.Column("ed25519_private_key_encrypted", sa.Text(), nullable=True),
        ),
        ("x25519_public_key", sa.Column("x25519_public_key", sa.Text(), nullable=True)),
        (
            "x25519_private_key_encrypted",
            sa.Column("x25519_private_key_encrypted", sa.Text(), nullable=True),
        ),
        (
            "current_key_version",
            sa.Column("current_key_version", sa.Integer(), nullable=False, server_default="1"),
        ),
    ]

    for column_name, column_def in columns_to_add:
        if column_name not in existing_columns:
            with op.batch_alter_table("instance_metadata", schema=None) as batch_op:
                batch_op.add_column(column_def)


def downgrade() -> None:
    """
    Remove the columns added by this migration.

    Note: This will fail if the columns were already present before this migration
    ran, which is expected behavior for safety.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_columns = {col["name"] for col in inspector.get_columns("instance_metadata")}

    columns_to_remove = [
        "ed25519_public_key",
        "ed25519_private_key_encrypted",
        "x25519_public_key",
        "x25519_private_key_encrypted",
        "current_key_version",
    ]

    for column_name in columns_to_remove:
        if column_name in existing_columns:
            with op.batch_alter_table("instance_metadata", schema=None) as batch_op:
                batch_op.drop_column(column_name)
