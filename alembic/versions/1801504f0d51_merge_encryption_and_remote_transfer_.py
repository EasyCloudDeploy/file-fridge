"""Merge encryption and remote transfer heads

Revision ID: 1801504f0d51
Revises: 16885c3bd99d, 3ccd8558b99e
Create Date: 2026-01-31 12:18:03.334665

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1801504f0d51'
down_revision: Union[str, None] = ('16885c3bd99d', '3ccd8558b99e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
