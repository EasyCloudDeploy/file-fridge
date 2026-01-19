"""Fix database schema inconsistencies

Revision ID: fix_schema_inconsistencies
Revises: a_unified_migration
Create Date: 2026-01-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fix_schema_inconsistencies"
down_revision: Union[str, None] = "1eab9db4e223"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("notifiers")}

    if "filter_level" not in existing_columns:
        op.execute(
            "ALTER TABLE notifiers ADD COLUMN filter_level VARCHAR(15) NOT NULL DEFAULT 'info'"
        )


def downgrade() -> None:
    op.drop_column("notifiers", "filter_level")
