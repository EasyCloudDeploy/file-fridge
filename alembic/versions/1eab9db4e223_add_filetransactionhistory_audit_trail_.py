"""Add FileTransactionHistory audit trail table

Revision ID: 1eab9db4e223
Revises: 3a6a9581dcf1
Create Date: 2026-01-17 21:04:01.004859

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1eab9db4e223"
down_revision: Union[str, None] = "3a6a9581dcf1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create file_transaction_history table for audit trail."""
    op.create_table(
        "file_transaction_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column(
            "transaction_type",
            sa.Enum(
                "freeze",
                "thaw",
                "move_cold",
                "delete",
                "copy",
                "restore",
                "cleanup",
                name="transaction_type",
            ),
            nullable=False,
        ),
        sa.Column("old_storage_type", sa.Enum("hot", "cold", name="storage_type"), nullable=True),
        sa.Column("new_storage_type", sa.Enum("hot", "cold", name="storage_type"), nullable=True),
        sa.Column(
            "old_status",
            sa.Enum("active", "moved", "deleted", "missing", "migrating", name="file_status"),
            nullable=True,
        ),
        sa.Column(
            "new_status",
            sa.Enum("active", "moved", "deleted", "missing", "migrating", name="file_status"),
            nullable=True,
        ),
        sa.Column("old_path", sa.String(), nullable=True),
        sa.Column("new_path", sa.String(), nullable=True),
        sa.Column("old_storage_location_id", sa.Integer(), nullable=True),
        sa.Column("new_storage_location_id", sa.Integer(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("checksum_before", sa.String(), nullable=True),
        sa.Column("checksum_after", sa.String(), nullable=True),
        sa.Column("operation_metadata", sa.Text(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("initiated_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["file_id"], ["file_inventory.id"], name=op.f("fk_file_transaction_history_file")
        ),
        sa.ForeignKeyConstraint(
            ["old_storage_location_id"],
            ["cold_storage_locations.id"],
            name=op.f("fk_file_transaction_history_old_storage_location"),
        ),
        sa.ForeignKeyConstraint(
            ["new_storage_location_id"],
            ["cold_storage_locations.id"],
            name=op.f("fk_file_transaction_history_new_storage_location"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_transaction_history")),
    )

    # Create indexes
    op.create_index(
        op.f("ix_file_transaction_history_file_id"), "file_transaction_history", ["file_id"]
    )
    op.create_index(
        op.f("ix_file_transaction_history_created_at"), "file_transaction_history", ["created_at"]
    )
    op.create_index(
        op.f("ix_file_transaction_history_transaction_type"),
        "file_transaction_history",
        ["transaction_type"],
    )
    op.create_index(
        op.f("ix_file_transaction_history_success"), "file_transaction_history", ["success"]
    )

    # Create composite indexes for common query patterns
    op.create_index(
        op.f("idx_history_file_type"), "file_transaction_history", ["file_id", "transaction_type"]
    )
    op.create_index(
        op.f("idx_history_time_range"), "file_transaction_history", ["created_at", "success"]
    )


def downgrade() -> None:
    """Drop file_transaction_history table."""
    # Drop composite indexes
    op.drop_index(op.f("idx_history_time_range"), "file_transaction_history")
    op.drop_index(op.f("idx_history_file_type"), "file_transaction_history")

    # Drop regular indexes
    op.drop_index(op.f("ix_file_transaction_history_success"), "file_transaction_history")
    op.drop_index(op.f("ix_file_transaction_history_transaction_type"), "file_transaction_history")
    op.drop_index(op.f("ix_file_transaction_history_created_at"), "file_transaction_history")
    op.drop_index(op.f("ix_file_transaction_history_file_id"), "file_transaction_history")

    # Drop table
    op.drop_table("file_transaction_history")
