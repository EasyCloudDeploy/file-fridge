"""Add local drive identity tracking fields to cold storage locations

Revision ID: b17d9f43c2aa
Revises: 9f3d6e2aa1b1
Create Date: 2026-04-16 09:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b17d9f43c2aa"
down_revision: Union[str, None] = "9f3d6e2aa1b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "cold_storage_locations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("cold_storage_locations")}

    with op.batch_alter_table("cold_storage_locations") as batch_op:
        if "local_drive_identifier" not in columns:
            batch_op.add_column(sa.Column("local_drive_identifier", sa.String(), nullable=True))
            batch_op.create_index(
                "ix_cold_storage_locations_local_drive_identifier",
                ["local_drive_identifier"],
                unique=False,
            )
        if "local_drive_label" not in columns:
            batch_op.add_column(sa.Column("local_drive_label", sa.String(), nullable=True))
        if "local_drive_mount_path" not in columns:
            batch_op.add_column(sa.Column("local_drive_mount_path", sa.String(), nullable=True))
        if "local_drive_is_removable" not in columns:
            batch_op.add_column(
                sa.Column(
                    "local_drive_is_removable",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "local_drive_is_connected" not in columns:
            batch_op.add_column(
                sa.Column(
                    "local_drive_is_connected",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )
        if "local_drive_last_seen_at" not in columns:
            batch_op.add_column(
                sa.Column("local_drive_last_seen_at", sa.DateTime(timezone=True), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "cold_storage_locations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("cold_storage_locations")}

    with op.batch_alter_table("cold_storage_locations") as batch_op:
        if "local_drive_last_seen_at" in columns:
            batch_op.drop_column("local_drive_last_seen_at")
        if "local_drive_is_connected" in columns:
            batch_op.drop_column("local_drive_is_connected")
        if "local_drive_is_removable" in columns:
            batch_op.drop_column("local_drive_is_removable")
        if "local_drive_mount_path" in columns:
            batch_op.drop_column("local_drive_mount_path")
        if "local_drive_label" in columns:
            batch_op.drop_column("local_drive_label")
        if "local_drive_identifier" in columns:
            batch_op.drop_index("ix_cold_storage_locations_local_drive_identifier")
            batch_op.drop_column("local_drive_identifier")

