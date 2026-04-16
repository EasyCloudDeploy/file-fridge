"""Normalize cold storage enum-like columns to SQLAlchemy enum names.

Revision ID: d4f9b8a1c2e3
Revises: c3e1d8f7aa42
Create Date: 2026-04-16 20:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f9b8a1c2e3"
down_revision: Union[str, None] = "c3e1d8f7aa42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "cold_storage_locations"):
        return

    columns = _column_names(inspector, "cold_storage_locations")

    if "backend_type" in columns:
        op.execute(
            """
            UPDATE cold_storage_locations
            SET backend_type = CASE
                WHEN backend_type IS NULL THEN NULL
                WHEN LOWER(TRIM(backend_type)) = 'local' THEN 'LOCAL'
                WHEN LOWER(TRIM(backend_type)) = 's3' THEN 'S3'
                WHEN LOWER(TRIM(backend_type)) = 'gdrive' THEN 'GDRIVE'
                ELSE backend_type
            END
            """
        )

    if "operation_mode" in columns:
        op.execute(
            """
            UPDATE cold_storage_locations
            SET operation_mode = CASE
                WHEN operation_mode IS NULL THEN NULL
                WHEN LOWER(TRIM(operation_mode)) = 'move' THEN 'MOVE'
                WHEN LOWER(TRIM(operation_mode)) = 'copy' THEN 'COPY'
                WHEN LOWER(TRIM(operation_mode)) = 'symlink' THEN 'SYMLINK'
                ELSE operation_mode
            END
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "cold_storage_locations"):
        return

    columns = _column_names(inspector, "cold_storage_locations")

    if "backend_type" in columns:
        op.execute(
            """
            UPDATE cold_storage_locations
            SET backend_type = LOWER(backend_type)
            WHERE backend_type IN ('LOCAL', 'S3', 'GDRIVE')
            """
        )

    if "operation_mode" in columns:
        op.execute(
            """
            UPDATE cold_storage_locations
            SET operation_mode = LOWER(operation_mode)
            WHERE operation_mode IN ('MOVE', 'COPY', 'SYMLINK')
            """
        )
