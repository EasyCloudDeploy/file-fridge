"""merge_v0_0_43_migration_paths

Merges two migration paths for v0.0.43:
- Direct path: da9c511bdeb2 -> 836bbd0f8c8d (consolidated)
- Incremental path: da9c511bdeb2 -> 63d866f824e9 -> 2c525e893192 -> f9251147202f

Both paths achieve the same database schema state, so this merge is a no-op.

Revision ID: 1c10588157df
Revises: 836bbd0f8c8d, f9251147202f
Create Date: 2026-01-24 21:16:40.460590

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "1c10588157df"
down_revision: Union[str, None] = ("836bbd0f8c8d", "f9251147202f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    No-op merge migration.

    Both parent migrations (836bbd0f8c8d and f9251147202f) result in
    the same database schema, so no additional changes are needed.

    This migration exists to unify the two upgrade paths:
    1. Fresh upgrade from v0.0.42 uses consolidated migration 836bbd0f8c8d
    2. Incremental upgrades from development versions use the path through
       63d866f824e9, 2c525e893192, and f9251147202f
    """


def downgrade() -> None:
    """
    No-op merge downgrade.

    Both parent migrations result in the same schema state,
    so no changes are needed when downgrading through this merge point.
    """
