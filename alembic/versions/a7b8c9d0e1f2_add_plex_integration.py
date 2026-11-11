"""Add Plex integration config and inventory play fields.

Revision ID: a7b8c9d0e1f2
Revises: 7377f34fc6a3
Create Date: 2026-11-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "7377f34fc6a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "plex_integration_config" not in tables:
        op.create_table(
            "plex_integration_config",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default=sa.text("'0'"), nullable=False),
            sa.Column("base_url", sa.String(), nullable=True),
            sa.Column("token_encrypted", sa.Text(), nullable=True),
            sa.Column("webhook_token", sa.String(), nullable=True),
            sa.Column("server_uuid", sa.String(), nullable=True),
            sa.Column(
                "tautulli_enabled", sa.Boolean(), server_default=sa.text("'0'"), nullable=False
            ),
            sa.Column("library_maps_json", sa.Text(), nullable=True),
            sa.Column("path_maps_json", sa.Text(), nullable=True),
            sa.Column("thaw_on_play", sa.Boolean(), server_default=sa.text("'1'"), nullable=False),
            sa.Column(
                "refresh_on_freeze", sa.Boolean(), server_default=sa.text("'1'"), nullable=False
            ),
            sa.Column(
                "refresh_on_thaw", sa.Boolean(), server_default=sa.text("'1'"), nullable=False
            ),
            sa.Column(
                "apply_cold_label", sa.Boolean(), server_default=sa.text("'0'"), nullable=False
            ),
            sa.Column("cold_label", sa.String(), nullable=True),
            sa.Column(
                "prethaw_on_deck", sa.Boolean(), server_default=sa.text("'1'"), nullable=False
            ),
            sa.Column(
                "poll_watch_state", sa.Boolean(), server_default=sa.text("'1'"), nullable=False
            ),
            sa.Column(
                "fridgefs_enabled", sa.Boolean(), server_default=sa.text("'0'"), nullable=False
            ),
            sa.Column("fridgefs_mount_path", sa.String(), nullable=True),
            sa.Column("fridgefs_cache_dir", sa.String(), nullable=True),
            sa.Column(
                "fridgefs_cache_max_gb", sa.Integer(), server_default=sa.text("20"), nullable=False
            ),
            sa.Column(
                "fridgefs_allow_other", sa.Boolean(), server_default=sa.text("'1'"), nullable=False
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_plex_integration_config_id", "plex_integration_config", ["id"])
        op.create_index(
            "ix_plex_integration_config_webhook_token",
            "plex_integration_config",
            ["webhook_token"],
        )

    if "file_inventory" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("file_inventory")}
    with op.batch_alter_table("file_inventory") as batch_op:
        if "last_plex_played_at" not in columns:
            batch_op.add_column(
                sa.Column("last_plex_played_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch_op.create_index("ix_file_inventory_last_plex_played_at", ["last_plex_played_at"])
        if "plex_rating_key" not in columns:
            batch_op.add_column(sa.Column("plex_rating_key", sa.String(), nullable=True))
            batch_op.create_index("ix_file_inventory_plex_rating_key", ["plex_rating_key"])
        if "plex_play_count" not in columns:
            batch_op.add_column(
                sa.Column(
                    "plex_play_count", sa.Integer(), server_default=sa.text("0"), nullable=False
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "file_inventory" in tables:
        columns = {column["name"] for column in inspector.get_columns("file_inventory")}
        with op.batch_alter_table("file_inventory") as batch_op:
            if "plex_play_count" in columns:
                batch_op.drop_column("plex_play_count")
            if "plex_rating_key" in columns:
                batch_op.drop_index("ix_file_inventory_plex_rating_key")
                batch_op.drop_column("plex_rating_key")
            if "last_plex_played_at" in columns:
                batch_op.drop_index("ix_file_inventory_last_plex_played_at")
                batch_op.drop_column("last_plex_played_at")
    if "plex_integration_config" in tables:
        op.drop_index(
            "ix_plex_integration_config_webhook_token", table_name="plex_integration_config"
        )
        op.drop_index("ix_plex_integration_config_id", table_name="plex_integration_config")
        op.drop_table("plex_integration_config")
