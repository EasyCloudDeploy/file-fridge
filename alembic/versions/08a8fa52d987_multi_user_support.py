"""multi-user support

Revision ID: 08a8fa52d987
Revises: add_instance_url_and_name
Create Date: 2026-01-29 19:10:46.958132

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08a8fa52d987"
down_revision: Union[str, None] = "add_instance_url_and_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add roles column with a default empty list
    op.add_column("users", sa.Column("roles", sa.JSON(), nullable=False, server_default="[]"))

    # If there is exactly one user, make them an admin to prevent lockout
    bind = op.get_bind()
    user_count = bind.execute(sa.text("SELECT count(*) FROM users")).scalar()
    if user_count == 1:
        bind.execute(sa.text("UPDATE users SET roles = '[\"admin\"]'"))


def downgrade() -> None:
    # Remove roles column
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("roles")
