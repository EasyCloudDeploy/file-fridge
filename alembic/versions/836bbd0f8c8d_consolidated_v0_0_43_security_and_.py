"""consolidated_v0_0_43_security_and_schema_fixes

Consolidated migration for v0.0.43 combining:
- Trust status enum uppercase update
- Request nonces timestamp index
- Remote connections schema fixes
- Various index optimizations

Revision ID: 836bbd0f8c8d
Revises: da9c511bdeb2
Create Date: 2026-01-24 21:15:35.556598

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "836bbd0f8c8d"
down_revision: Union[str, None] = "da9c511bdeb2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Consolidated upgrade for v0.0.43.

    Combines migrations:
    - 63d866f824e9: Update TrustStatus enum values to uppercase
    - 2c525e893192: Add index to request_nonces.timestamp
    - f9251147202f: Fix remote_connections schema and index cleanup
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # 1. Update TrustStatus enum values to uppercase (from 63d866f824e9)
    # ========================================================================
    op.execute(
        "UPDATE remote_connections SET trust_status = UPPER(trust_status) "
        "WHERE trust_status IS NOT NULL"
    )

    # Ensure trust_status column uses proper enum type
    with op.batch_alter_table("remote_connections", schema=None) as batch_op:
        batch_op.alter_column(
            "trust_status",
            existing_type=sa.VARCHAR(),
            type_=sa.Enum("PENDING", "TRUSTED", "REJECTED", name="truststatus"),
            existing_nullable=False,
            server_default=sa.text("'PENDING'"),
        )

    # ========================================================================
    # 2. Add index to request_nonces.timestamp (from 2c525e893192)
    # ========================================================================
    indexes = inspector.get_indexes("request_nonces")
    index_names = [idx["name"] for idx in indexes]

    if "ix_request_nonces_timestamp" not in index_names:
        op.create_index(
            op.f("ix_request_nonces_timestamp"),
            "request_nonces",
            ["timestamp"],
            unique=False,
        )

    # ========================================================================
    # 3. Schema fixes and index cleanup (from f9251147202f)
    # ========================================================================

    # Update file_inventory.status enum if needed
    with op.batch_alter_table("file_inventory", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.VARCHAR(length=7),
            type_=sa.Enum("ACTIVE", "MOVED", "DELETED", "MISSING", "MIGRATING", name="filestatus"),
            existing_nullable=True,
        )

    # Update file_transaction_history.transaction_type enum
    with op.batch_alter_table("file_transaction_history", schema=None) as batch_op:
        batch_op.alter_column(
            "transaction_type",
            existing_type=sa.VARCHAR(length=9),
            type_=sa.Enum(
                "FREEZE",
                "THAW",
                "MOVE_COLD",
                "DELETE",
                "COPY",
                "RESTORE",
                "CLEANUP",
                "REMOTE_MIGRATE",
                "REMOTE_RECEIVE",
                name="transactiontype",
            ),
            existing_nullable=False,
        )

    # Drop redundant indexes (these were automatically created by older migrations)
    # Use try-except to make idempotent
    indexes_to_drop = [
        ("file_inventory", "idx_file_inventory_cold_storage_location_id"),
        ("file_inventory", "idx_inventory_checksum"),
        ("file_records", "idx_file_records_cold_storage_location_id"),
        ("file_tags", "idx_file_tags_file_id"),
        ("file_tags", "idx_file_tags_tag_id"),
        ("tag_rules", "idx_tag_rules_enabled"),
        ("tag_rules", "idx_tag_rules_priority"),
        ("tag_rules", "idx_tag_rules_tag_id"),
        ("tags", "idx_tags_name"),
    ]

    for table_name, index_name in indexes_to_drop:
        table_indexes = inspector.get_indexes(table_name)
        table_index_names = [idx["name"] for idx in table_indexes]
        if index_name in table_index_names:
            op.drop_index(op.f(index_name), table_name=table_name)

    # Ensure notifiers columns have correct constraints
    with op.batch_alter_table("notifiers", schema=None) as batch_op:
        batch_op.alter_column("id", existing_type=sa.INTEGER(), nullable=False, autoincrement=True)
        batch_op.alter_column("subscribed_events", existing_type=sqlite.JSON(), nullable=False)


def downgrade() -> None:
    """
    Consolidated downgrade from v0.0.43.

    This reverses all changes made in the consolidated upgrade.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Revert notifiers column changes
    with op.batch_alter_table("notifiers", schema=None) as batch_op:
        batch_op.alter_column("subscribed_events", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("id", existing_type=sa.INTEGER(), nullable=True, autoincrement=True)

    # Recreate dropped indexes
    indexes_to_recreate = [
        ("tags", "idx_tags_name", ["name"]),
        ("tag_rules", "idx_tag_rules_tag_id", ["tag_id"]),
        ("tag_rules", "idx_tag_rules_priority", ["priority"]),
        ("tag_rules", "idx_tag_rules_enabled", ["enabled"]),
        ("file_tags", "idx_file_tags_tag_id", ["tag_id"]),
        ("file_tags", "idx_file_tags_file_id", ["file_id"]),
        ("file_records", "idx_file_records_cold_storage_location_id", ["cold_storage_location_id"]),
        ("file_inventory", "idx_inventory_checksum", ["checksum"]),
        (
            "file_inventory",
            "idx_file_inventory_cold_storage_location_id",
            ["cold_storage_location_id"],
        ),
    ]

    for table_name, index_name, columns in indexes_to_recreate:
        table_indexes = inspector.get_indexes(table_name)
        table_index_names = [idx["name"] for idx in table_indexes]
        if index_name not in table_index_names:
            op.create_index(op.f(index_name), table_name, columns, unique=False)

    # Revert file_transaction_history.transaction_type enum
    with op.batch_alter_table("file_transaction_history", schema=None) as batch_op:
        batch_op.alter_column(
            "transaction_type",
            existing_type=sa.Enum(
                "FREEZE",
                "THAW",
                "MOVE_COLD",
                "DELETE",
                "COPY",
                "RESTORE",
                "CLEANUP",
                "REMOTE_MIGRATE",
                "REMOTE_RECEIVE",
                name="transactiontype",
            ),
            type_=sa.VARCHAR(length=9),
            existing_nullable=False,
        )

    # Revert file_inventory.status enum
    with op.batch_alter_table("file_inventory", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(
                "ACTIVE", "MOVED", "DELETED", "MISSING", "MIGRATING", name="filestatus"
            ),
            type_=sa.VARCHAR(length=7),
            existing_nullable=True,
        )

    # Drop request_nonces.timestamp index
    indexes = inspector.get_indexes("request_nonces")
    index_names = [idx["name"] for idx in indexes]
    if "ix_request_nonces_timestamp" in index_names:
        op.drop_index(op.f("ix_request_nonces_timestamp"), table_name="request_nonces")

    # Revert trust_status to lowercase
    with op.batch_alter_table("remote_connections", schema=None) as batch_op:
        batch_op.alter_column(
            "trust_status",
            existing_type=sa.Enum("PENDING", "TRUSTED", "REJECTED", name="truststatus"),
            type_=sa.VARCHAR(),
            existing_nullable=False,
            server_default=sa.text("'pending'"),
        )

    op.execute(
        "UPDATE remote_connections SET trust_status = LOWER(trust_status) "
        "WHERE trust_status IS NOT NULL"
    )
