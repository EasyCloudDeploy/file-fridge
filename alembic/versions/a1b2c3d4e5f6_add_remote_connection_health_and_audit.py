"""Add health tracking columns, audit log table, and path permissions table.

Revision ID: a1b2c3d4e5f6
Revises: d4f9b8a1c2e3
Create Date: 2026-04-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d4f9b8a1c2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(c["name"] == column for c in inspector.get_columns(table))


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table in inspector.get_table_names()


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table))


def upgrade() -> None:
    # Add health tracking columns to remote_connections (idempotent)
    if _table_exists("remote_connections"):
        rc_missing = []
        if not _column_exists("remote_connections", "last_seen_at"):
            rc_missing.append(
                sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
            )
        if not _column_exists("remote_connections", "is_reachable"):
            rc_missing.append(
                sa.Column(
                    "is_reachable",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("'1'"),
                )
            )
        if rc_missing:
            with op.batch_alter_table("remote_connections") as batch_op:
                for col in rc_missing:
                    batch_op.add_column(col)

    # Add created_at / updated_at to remote_transfer_jobs (idempotent)
    if _table_exists("remote_transfer_jobs"):
        rtj_missing = []
        if not _column_exists("remote_transfer_jobs", "created_at"):
            rtj_missing.append(
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.text("(CURRENT_TIMESTAMP)"),
                    nullable=True,
                )
            )
        if not _column_exists("remote_transfer_jobs", "updated_at"):
            rtj_missing.append(
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
            )
        if rtj_missing:
            with op.batch_alter_table("remote_transfer_jobs") as batch_op:
                for col in rtj_missing:
                    batch_op.add_column(col)

    # Create remote_audit_logs table (idempotent)
    if not _table_exists("remote_audit_logs"):
        op.create_table(
            "remote_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column(
                "timestamp",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column(
                "connection_id",
                sa.Integer(),
                sa.ForeignKey("remote_connections.id"),
                nullable=True,
            ),
            sa.Column("connection_name", sa.String(255), nullable=True),
            sa.Column("direction", sa.String(10), nullable=True),
            sa.Column("file_path", sa.String(1024), nullable=True),
            sa.Column("file_size", sa.BigInteger(), nullable=True),
            sa.Column("checksum", sa.String(64), nullable=True),
            sa.Column("status", sa.String(20), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
    if _table_exists("remote_audit_logs"):
        if not _index_exists("remote_audit_logs", "ix_remote_audit_logs_timestamp"):
            op.create_index(
                "ix_remote_audit_logs_timestamp",
                "remote_audit_logs",
                ["timestamp"],
                unique=False,
            )
        if not _index_exists(
            "remote_audit_logs", "ix_remote_audit_logs_connection_id_timestamp"
        ):
            op.create_index(
                "ix_remote_audit_logs_connection_id_timestamp",
                "remote_audit_logs",
                ["connection_id", "timestamp"],
                unique=False,
            )
        if not _index_exists("remote_audit_logs", "ix_remote_audit_logs_event_type"):
            op.create_index(
                "ix_remote_audit_logs_event_type",
                "remote_audit_logs",
                ["event_type"],
                unique=False,
            )
        if not _index_exists("remote_audit_logs", "ix_remote_audit_logs_status"):
            op.create_index(
                "ix_remote_audit_logs_status",
                "remote_audit_logs",
                ["status"],
                unique=False,
            )

    # Create remote_connection_path_permissions table (idempotent)
    if not _table_exists("remote_connection_path_permissions"):
        op.create_table(
            "remote_connection_path_permissions",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column(
                "remote_connection_id",
                sa.Integer(),
                sa.ForeignKey("remote_connections.id"),
                nullable=False,
            ),
            sa.Column(
                "monitored_path_id",
                sa.Integer(),
                sa.ForeignKey("monitored_paths.id"),
                nullable=False,
            ),
            sa.Column(
                "can_browse",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("'1'"),
            ),
            sa.Column(
                "can_pull",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("'1'"),
            ),
            sa.UniqueConstraint(
                "remote_connection_id", "monitored_path_id", name="uq_conn_path_perm"
            ),
        )


def downgrade() -> None:
    if _table_exists("remote_connection_path_permissions"):
        op.drop_table("remote_connection_path_permissions")
    if _table_exists("remote_audit_logs"):
        if _index_exists("remote_audit_logs", "ix_remote_audit_logs_status"):
            op.drop_index("ix_remote_audit_logs_status", table_name="remote_audit_logs")
        if _index_exists("remote_audit_logs", "ix_remote_audit_logs_event_type"):
            op.drop_index("ix_remote_audit_logs_event_type", table_name="remote_audit_logs")
        if _index_exists(
            "remote_audit_logs", "ix_remote_audit_logs_connection_id_timestamp"
        ):
            op.drop_index(
                "ix_remote_audit_logs_connection_id_timestamp",
                table_name="remote_audit_logs",
            )
        if _index_exists("remote_audit_logs", "ix_remote_audit_logs_timestamp"):
            op.drop_index("ix_remote_audit_logs_timestamp", table_name="remote_audit_logs")
        op.drop_table("remote_audit_logs")

    if _table_exists("remote_transfer_jobs"):
        rtj_present = [
            c
            for c in ["updated_at", "created_at"]
            if _column_exists("remote_transfer_jobs", c)
        ]
        if rtj_present:
            with op.batch_alter_table("remote_transfer_jobs") as batch_op:
                for col in rtj_present:
                    batch_op.drop_column(col)

    if _table_exists("remote_connections"):
        rc_present = [
            c
            for c in ["is_reachable", "last_seen_at"]
            if _column_exists("remote_connections", c)
        ]
        if rc_present:
            with op.batch_alter_table("remote_connections") as batch_op:
                for col in rc_present:
                    batch_op.drop_column(col)
