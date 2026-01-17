"""Add last_scan_at, last_scan_status, last_scan_error_log to monitored_paths

Revision ID: e491b693dbeb
Revises: c3f62354598f
Create Date: 2026-01-17 11:47:00.410638

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e491b693dbeb'
down_revision: Union[str, None] = 'c3f62354598f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns for tracking last scan results
    op.add_column('monitored_paths', sa.Column('last_scan_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('monitored_paths', sa.Column('last_scan_status', sa.Enum('SUCCESS', 'FAILURE', 'PENDING', name='scanstatus'), nullable=True))
    op.add_column('monitored_paths', sa.Column('last_scan_error_log', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('monitored_paths', 'last_scan_error_log')
    op.drop_column('monitored_paths', 'last_scan_status')
    op.drop_column('monitored_paths', 'last_scan_at')
