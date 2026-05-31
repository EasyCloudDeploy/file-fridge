"""add paused to cold_storage_locations and psk_encrypted to p2p_network_config

Revision ID: 099b10f2eff5
Revises: e6f7a8b9c0d1
Create Date: 2026-05-31 15:44:19.848900

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '099b10f2eff5'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    # SQLite supports ALTER TABLE ADD COLUMN natively; skip if already present
    # (handles databases that received the column via SQLAlchemy create_all).
    if not _has_column("cold_storage_locations", "paused"):
        op.execute("ALTER TABLE cold_storage_locations ADD COLUMN paused BOOLEAN NOT NULL DEFAULT 0")

    if not _has_column("p2p_network_config", "psk_encrypted"):
        op.execute("ALTER TABLE p2p_network_config ADD COLUMN psk_encrypted TEXT")


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode.
    if _has_column("p2p_network_config", "psk_encrypted"):
        with op.batch_alter_table("p2p_network_config") as batch_op:
            batch_op.drop_column("psk_encrypted")

    if _has_column("cold_storage_locations", "paused"):
        with op.batch_alter_table("cold_storage_locations") as batch_op:
            batch_op.drop_column("paused")
