"""Add conflict_resolution to remote_transfer_jobs

Revision ID: c31860e1a401
Revises: 5c67e9e65fc0
Create Date: 2026-01-30 23:22:52.731560

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c31860e1a401'
down_revision: Union[str, None] = '5c67e9e65fc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add conflict_resolution column to remote_transfer_jobs table
    op.add_column(
        'remote_transfer_jobs',
        sa.Column(
            'conflict_resolution',
            sa.String(length=9),
            nullable=False,
            server_default='OVERWRITE'
        )
    )


def downgrade() -> None:
    # Remove conflict_resolution column from remote_transfer_jobs table
    op.drop_column('remote_transfer_jobs', 'conflict_resolution')
