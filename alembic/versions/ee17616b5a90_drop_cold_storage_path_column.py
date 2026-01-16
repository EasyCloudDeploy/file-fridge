"""drop_cold_storage_path_column

Revision ID: ee17616b5a90
Revises: 1660b8cad608
Create Date: 2026-01-08 06:52:13.801654

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ee17616b5a90"
down_revision: Union[str, None] = "1660b8cad608"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the legacy cold_storage_path column from monitored_paths table."""
    # SQLite doesn't support DROP COLUMN directly, so we need to recreate the table
    # Get the bind to execute raw SQL
    bind = op.get_bind()

    # Create new table without cold_storage_path
    bind.execute(sa.text("""
        CREATE TABLE monitored_paths_new (
            id INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            operation_type VARCHAR(7),
            check_interval_seconds INTEGER,
            enabled BOOLEAN,
            prevent_indexing BOOLEAN NOT NULL,
            error_message TEXT,
            created_at DATETIME DEFAULT (CURRENT_TIMESTAMP),
            updated_at DATETIME,
            PRIMARY KEY (id)
        )
    """))

    # Copy data from old table to new table (excluding cold_storage_path)
    bind.execute(sa.text("""
        INSERT INTO monitored_paths_new
        SELECT id, name, source_path, operation_type, check_interval_seconds,
               enabled, prevent_indexing, error_message, created_at, updated_at
        FROM monitored_paths
    """))

    # Drop old table
    bind.execute(sa.text("DROP TABLE monitored_paths"))

    # Rename new table to original name
    bind.execute(sa.text("ALTER TABLE monitored_paths_new RENAME TO monitored_paths"))

    # Recreate indices
    bind.execute(sa.text("CREATE INDEX ix_monitored_paths_id ON monitored_paths (id)"))
    bind.execute(sa.text("CREATE INDEX ix_monitored_paths_name ON monitored_paths (name)"))


def downgrade() -> None:
    """Add back cold_storage_path column (will be empty after downgrade)."""
    # Add column back as nullable since we can't restore the data
    op.add_column("monitored_paths", sa.Column("cold_storage_path", sa.String(), nullable=True))
