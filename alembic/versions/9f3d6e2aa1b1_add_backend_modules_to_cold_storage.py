"""Add backend metadata fields to cold storage locations

Revision ID: 9f3d6e2aa1b1
Revises: 764abe6a5a03
Create Date: 2026-04-15 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9f3d6e2aa1b1"
down_revision: Union[str, None] = "764abe6a5a03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "cold_storage_locations" not in tables:
        return

    column_names = {column["name"] for column in inspector.get_columns("cold_storage_locations")}

    if "backend_type" not in column_names:
        op.add_column(
            "cold_storage_locations",
            sa.Column("backend_type", sa.String(length=16), nullable=True, server_default="local"),
        )

    if "operation_mode" not in column_names:
        op.add_column(
            "cold_storage_locations",
            sa.Column("operation_mode", sa.String(length=16), nullable=True, server_default="move"),
        )

    if "backend_config_encrypted" not in column_names:
        op.add_column(
            "cold_storage_locations",
            sa.Column("backend_config_encrypted", sa.Text(), nullable=True),
        )

    # Backfill and enforce non-null defaults for existing rows.
    op.execute(
        """
        UPDATE cold_storage_locations
        SET backend_type = COALESCE(backend_type, 'local')
        """
    )

    if {"monitored_paths", "path_storage_location_association"} <= tables:
        op.execute(
            """
            UPDATE cold_storage_locations
            SET operation_mode = COALESCE(
                operation_mode,
                (
                    SELECT mp.operation_type
                    FROM monitored_paths mp
                    JOIN path_storage_location_association psa ON psa.path_id = mp.id
                    WHERE psa.storage_location_id = cold_storage_locations.id
                    ORDER BY mp.id
                    LIMIT 1
                ),
                'move'
            )
            """
        )
    else:
        op.execute(
            """
            UPDATE cold_storage_locations
            SET operation_mode = COALESCE(operation_mode, 'move')
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "cold_storage_locations" not in tables:
        return

    column_names = {column["name"] for column in inspector.get_columns("cold_storage_locations")}

    with op.batch_alter_table("cold_storage_locations") as batch_op:
        if "backend_config_encrypted" in column_names:
            batch_op.drop_column("backend_config_encrypted")
        if "operation_mode" in column_names:
            batch_op.drop_column("operation_mode")
        if "backend_type" in column_names:
            batch_op.drop_column("backend_type")
