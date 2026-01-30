"""merge multi-user and bidirectional heads

Revision ID: 5c67e9e65fc0
Revises: 08a8fa52d987, add_bidirectional_transfer_support
Create Date: 2026-01-29 21:22:05.751456

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c67e9e65fc0'
down_revision: Union[str, None] = ('08a8fa52d987', 'add_bidirectional_transfer_support')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
