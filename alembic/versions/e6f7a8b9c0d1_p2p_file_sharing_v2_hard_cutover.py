"""P2P file sharing v2 hard cutover.

Revision ID: e6f7a8b9c0d1
Revises: a1b2c3d4e5f6
Create Date: 2026-04-26 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


P2P_PEER_STATUS = sa.Enum("CONNECTED", "DEGRADED", "DISCONNECTED", name="p2ppeerstatus")


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table in inspector.get_table_names()


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    # Local file share policy flag.
    if _table_exists("file_inventory") and not _column_exists("file_inventory", "is_shareable"):
        with op.batch_alter_table("file_inventory") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_shareable",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("'1'"),
                )
            )
            batch_op.create_index("ix_file_inventory_is_shareable", ["is_shareable"], unique=False)

    if not _table_exists("p2p_network_config"):
        op.create_table(
            "p2p_network_config",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("network_name", sa.String(), nullable=False),
            sa.Column("psk_hash", sa.String(), nullable=False, unique=True),
            sa.Column("listen_host", sa.String(), nullable=False, server_default=sa.text("'0.0.0.0'")),
            sa.Column("listen_port", sa.Integer(), nullable=False, server_default=sa.text("9119")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("'1'")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_p2p_network_config_psk_hash", "p2p_network_config", ["psk_hash"])

    if not _table_exists("p2p_peers"):
        op.create_table(
            "p2p_peers",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("peer_name", sa.String(), nullable=False),
            sa.Column("peer_id", sa.String(), nullable=False, unique=True),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("status", P2P_PEER_STATUS, nullable=False, server_default=sa.text("'DISCONNECTED'")),
            sa.Column("psk_hash", sa.String(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_p2p_peers_peer_id", "p2p_peers", ["peer_id"])
        op.create_index("ix_p2p_peers_psk_hash", "p2p_peers", ["psk_hash"])

    if not _table_exists("remote_shared_file_cache"):
        op.create_table(
            "remote_shared_file_cache",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("peer_id", sa.Integer(), sa.ForeignKey("p2p_peers.id"), nullable=False),
            sa.Column("remote_file_id", sa.String(), nullable=False),
            sa.Column("path_id", sa.Integer(), nullable=True),
            sa.Column("file_path", sa.String(), nullable=False),
            sa.Column("display_file_path", sa.String(), nullable=False),
            sa.Column("relative_path", sa.String(), nullable=True),
            sa.Column("storage_type", sa.String(), nullable=False),
            sa.Column("file_size", sa.BigInteger(), nullable=False),
            sa.Column("file_mtime", sa.DateTime(timezone=True), nullable=True),
            sa.Column("checksum", sa.String(), nullable=True),
            sa.Column("mime_type", sa.String(), nullable=True),
            sa.Column("file_extension", sa.String(), nullable=True),
            sa.Column("path_name", sa.String(), nullable=True),
            sa.Column(
                "last_announced_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "peer_id",
                "remote_file_id",
                name="uq_remote_shared_file_peer_remote_id",
            ),
        )
        op.create_index("ix_remote_shared_file_cache_peer_id", "remote_shared_file_cache", ["peer_id"])
        op.create_index(
            "ix_remote_shared_file_cache_remote_file_id",
            "remote_shared_file_cache",
            ["remote_file_id"],
        )
        op.create_index("ix_remote_shared_file_cache_file_path", "remote_shared_file_cache", ["file_path"])
        op.create_index(
            "ix_remote_shared_file_cache_file_extension",
            "remote_shared_file_cache",
            ["file_extension"],
        )

    # Hard cutover: remove legacy protocol tables and all existing remote links/transfers.
    legacy_tables = [
        "remote_transfer_jobs",
        "remote_connection_path_permissions",
        "remote_audit_logs",
        "request_nonces",
        "remote_connections",
    ]
    for table_name in legacy_tables:
        if _table_exists(table_name):
            op.drop_table(table_name)


def downgrade() -> None:
    if _table_exists("remote_shared_file_cache"):
        op.drop_table("remote_shared_file_cache")
    if _table_exists("p2p_peers"):
        op.drop_table("p2p_peers")
    if _table_exists("p2p_network_config"):
        op.drop_table("p2p_network_config")

    if _table_exists("file_inventory") and _column_exists("file_inventory", "is_shareable"):
        with op.batch_alter_table("file_inventory") as batch_op:
            batch_op.drop_index("ix_file_inventory_is_shareable")
            batch_op.drop_column("is_shareable")
