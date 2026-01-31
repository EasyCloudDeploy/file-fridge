"""Add conflict_resolution to remote_transfer_jobs

Revision ID: c31860e1a401
Revises: 5c67e9e65fc0
Create Date: 2026-01-30 23:22:52.731560

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c31860e1a401"
down_revision: Union[str, None] = "5c67e9e65fc0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type for PostgreSQL (no-op for SQLite)
    conflict_resolution_enum = sa.Enum(
        "SKIP",
        "OVERWRITE",
        "RENAME",
        "COMPARE",
        name="conflictresolution",
        create_type=True,
    )

    # Add conflict_resolution column to remote_transfer_jobs table
    op.add_column(
        "remote_transfer_jobs",
        sa.Column(
            "conflict_resolution",
            conflict_resolution_enum,
            nullable=False,
            server_default="OVERWRITE",
        ),
    )


def downgrade() -> None:
    # Remove conflict_resolution column from remote_transfer_jobs table
    op.drop_column("remote_transfer_jobs", "conflict_resolution")

    # Drop enum type for PostgreSQL (no-op for SQLite)
    sa.Enum(name="conflictresolution").drop(op.get_bind(), checkfirst=True)
