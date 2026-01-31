"""Fix remote_connections schema

Revision ID: f9251147202f
Revises: 2c525e893192
Create Date: 2026-01-24 20:51:36.560672

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine.reflection import Inspector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9251147202f"
down_revision: Union[str, None] = "2c525e893192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch_alter_table for SQLite compatibility
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)

    # Helper to check if index exists
    def index_exists(table_name, index_name):
        indexes = inspector.get_indexes(table_name)
        return any(idx["name"] == index_name for idx in indexes)

    with op.batch_alter_table("file_inventory", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.VARCHAR(length=7),
            type_=sa.Enum("ACTIVE", "MOVED", "DELETED", "MISSING", "MIGRATING", name="filestatus"),
            existing_nullable=True
        )
        if index_exists("file_inventory", "idx_file_inventory_cold_storage_location_id"):
            batch_op.drop_index("idx_file_inventory_cold_storage_location_id")
        if index_exists("file_inventory", "idx_inventory_checksum"):
            batch_op.drop_index("idx_inventory_checksum")

    with op.batch_alter_table("file_records", schema=None) as batch_op:
        if index_exists("file_records", "idx_file_records_cold_storage_location_id"):
            batch_op.drop_index("idx_file_records_cold_storage_location_id")

    with op.batch_alter_table("file_tags", schema=None) as batch_op:
        if index_exists("file_tags", "idx_file_tags_file_id"):
            batch_op.drop_index("idx_file_tags_file_id")
        if index_exists("file_tags", "idx_file_tags_tag_id"):
            batch_op.drop_index("idx_file_tags_tag_id")

    with op.batch_alter_table("file_transaction_history", schema=None) as batch_op:
        batch_op.alter_column(
            "transaction_type",
            existing_type=sa.VARCHAR(length=9),
            type_=sa.Enum(
                "FREEZE", "THAW", "MOVE_COLD", "DELETE", "COPY",
                "RESTORE", "CLEANUP", "REMOTE_MIGRATE", "REMOTE_RECEIVE",
                name="transactiontype"
            ),
            existing_nullable=False
        )

    with op.batch_alter_table("notifiers", schema=None) as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.INTEGER(),
            nullable=False,
            autoincrement=True
        )
        batch_op.alter_column(
            "subscribed_events",
            existing_type=sqlite.JSON(),
            nullable=False
        )

    # Drop old remote_connections columns and add new ones using batch
    # IMPORTANT: For SQLite batch mode, we must be careful with indexes on dropped columns.
    # The safest way is to drop the index FIRST in a separate operation if possible,
    # OR rely on `batch_alter_table` handling it if we don't mention it?
    # Actually, if we drop the column, the index *should* go with it in standard SQL,
    # but Alembic batch might try to recreate it if it sees it in metadata.

    # Let's try to explicitly drop the index first if it exists, OUTSIDE the batch if possible?
    # No, SQLite doesn't support DROP INDEX normally inside transaction if it's locked?
    # Batch is the way.

    with op.batch_alter_table("remote_connections", schema=None) as batch_op:
        # Check if columns exist before dropping
        columns = [c["name"] for c in inspector.get_columns("remote_connections")]

        # If we are dropping remote_instance_uuid, any index on it must be dropped too.
        if "remote_instance_uuid" in columns:
            if index_exists("remote_connections", "idx_remote_connections_remote_instance_uuid"):
                batch_op.drop_index("idx_remote_connections_remote_instance_uuid")
            # Also check for other names it might have
            if index_exists("remote_connections", "ix_remote_connections_remote_instance_uuid"):
                batch_op.drop_index("ix_remote_connections_remote_instance_uuid")

            batch_op.drop_column("remote_instance_uuid")

        if "shared_secret" in columns:
            batch_op.drop_column("shared_secret")

        if "remote_fingerprint" not in columns:
            batch_op.add_column(sa.Column("remote_fingerprint", sa.String(), nullable=True))
        if "remote_ed25519_public_key" not in columns:
            batch_op.add_column(sa.Column("remote_ed25519_public_key", sa.Text(), nullable=True))
        if "remote_x25519_public_key" not in columns:
            batch_op.add_column(sa.Column("remote_x25519_public_key", sa.Text(), nullable=True))
        if "trust_status" not in columns:
            batch_op.add_column(
                sa.Column(
                    "trust_status",
                    sa.Enum("PENDING", "TRUSTED", "REJECTED", name="truststatus"),
                    nullable=False,
                    server_default=sa.text("'PENDING'")
                )
            )

        # Index creation for new column
        if not index_exists("remote_connections", "ix_remote_connections_remote_fingerprint"):
            batch_op.create_index(op.f("ix_remote_connections_remote_fingerprint"), ["remote_fingerprint"], unique=True)

    with op.batch_alter_table("tag_rules", schema=None) as batch_op:
        if index_exists("tag_rules", "idx_tag_rules_enabled"):
            batch_op.drop_index("idx_tag_rules_enabled")
        if index_exists("tag_rules", "idx_tag_rules_priority"):
            batch_op.drop_index("idx_tag_rules_priority")
        if index_exists("tag_rules", "idx_tag_rules_tag_id"):
            batch_op.drop_index("idx_tag_rules_tag_id")

    with op.batch_alter_table("tags", schema=None) as batch_op:
        if index_exists("tags", "idx_tags_name"):
            batch_op.drop_index("idx_tags_name")


def downgrade() -> None:
    # Use batch_alter_table for SQLite compatibility
    # Note: Downgrade logic is harder to make fully robust without similar checks,
    # but for now we focus on upgrade working.
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.create_index("idx_tags_name", ["name"], unique=False)

    with op.batch_alter_table("tag_rules", schema=None) as batch_op:
        batch_op.create_index("idx_tag_rules_tag_id", ["tag_id"], unique=False)
        batch_op.create_index("idx_tag_rules_priority", ["priority"], unique=False)
        batch_op.create_index("idx_tag_rules_enabled", ["enabled"], unique=False)

    # Revert remote_connections changes
    with op.batch_alter_table("remote_connections", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_remote_connections_remote_fingerprint"))
        batch_op.drop_column("trust_status")
        batch_op.drop_column("remote_x25519_public_key")
        batch_op.drop_column("remote_ed25519_public_key")
        batch_op.drop_column("remote_fingerprint")

        batch_op.add_column(sa.Column("shared_secret", sa.VARCHAR(), nullable=False))
        batch_op.add_column(sa.Column("remote_instance_uuid", sa.TEXT(), nullable=True))
        batch_op.create_index("idx_remote_connections_remote_instance_uuid", ["remote_instance_uuid"], unique=1)

    with op.batch_alter_table("notifiers", schema=None) as batch_op:
        batch_op.alter_column(
            "subscribed_events",
            existing_type=sqlite.JSON(),
            nullable=True
        )
        batch_op.alter_column(
            "id",
            existing_type=sa.INTEGER(),
            nullable=True,
            autoincrement=True
        )

    with op.batch_alter_table("file_transaction_history", schema=None) as batch_op:
        batch_op.alter_column(
            "transaction_type",
            existing_type=sa.Enum("FREEZE", "THAW", "MOVE_COLD", "DELETE", "COPY", "RESTORE", "CLEANUP", "REMOTE_MIGRATE", "REMOTE_RECEIVE", name="transactiontype"),
            type_=sa.VARCHAR(length=9),
            existing_nullable=False
        )

    with op.batch_alter_table("file_tags", schema=None) as batch_op:
        batch_op.create_index("idx_file_tags_tag_id", ["tag_id"], unique=False)
        batch_op.create_index("idx_file_tags_file_id", ["file_id"], unique=False)

    with op.batch_alter_table("file_records", schema=None) as batch_op:
        batch_op.create_index("idx_file_records_cold_storage_location_id", ["cold_storage_location_id"], unique=False)

    with op.batch_alter_table("file_inventory", schema=None) as batch_op:
        batch_op.create_index("idx_inventory_checksum", ["checksum"], unique=False)
        batch_op.create_index("idx_file_inventory_cold_storage_location_id", ["cold_storage_location_id"], unique=False)
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum("ACTIVE", "MOVED", "DELETED", "MISSING", "MIGRATING", name="filestatus"),
            type_=sa.VARCHAR(length=7),
            existing_nullable=True
        )
