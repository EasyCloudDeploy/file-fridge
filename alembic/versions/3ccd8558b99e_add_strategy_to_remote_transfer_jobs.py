"""add strategy to remote transfer jobs

Revision ID: 3ccd8558b99e
Revises: c31860e1a401
Create Date: 2026-01-31 00:02:44.660014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ccd8558b99e'
down_revision: Union[str, None] = 'c31860e1a401'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type for PostgreSQL (no-op for SQLite)
    file_transfer_strategy_enum = sa.Enum(
        "COPY",
        "MOVE",
        name="filetransferstrategy",
        create_type=True,
    )

    # Add strategy column to remote_transfer_jobs table
    op.add_column(
        "remote_transfer_jobs",
        sa.Column(
            "strategy",
            file_transfer_strategy_enum,
            nullable=False,
            server_default=sa.text("'COPY'"),
        ),
    )


def downgrade() -> None:
    # Remove strategy column from remote_transfer_jobs table
    op.drop_column("remote_transfer_jobs", "strategy")

    # Drop enum type for PostgreSQL (no-op for SQLite)
    sa.Enum(name="filetransferstrategy").drop(op.get_bind(), checkfirst=True)
