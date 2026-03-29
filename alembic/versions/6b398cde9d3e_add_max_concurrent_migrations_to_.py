"""Add max_concurrent_migrations to MonitoredPath

Revision ID: 6b398cde9d3e
Revises: 726412e8862d
Create Date: 2026-03-29 00:43:18.778963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b398cde9d3e'
down_revision: Union[str, None] = '726412e8862d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "monitored_paths",
        sa.Column(
            "max_concurrent_migrations", sa.Integer(), nullable=False, server_default="3"
        ),
    )


def downgrade() -> None:
    op.drop_column("monitored_paths", "max_concurrent_migrations")
