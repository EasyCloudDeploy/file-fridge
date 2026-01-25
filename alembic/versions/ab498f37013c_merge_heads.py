"""merge heads

Revision ID: ab498f37013c
Revises: add_notification_encryption, d2a9627cb2b1
Create Date: 2026-01-21 19:18:48.227169

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "ab498f37013c"
down_revision: Union[str, None] = ("add_notification_encryption", "d2a9627cb2b1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
