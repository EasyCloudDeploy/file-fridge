"""Add max_concurrent_migrations to MonitoredPath

Revision ID: 6b398cde9d3e
Revises: 726412e8862d
Create Date: 2026-03-29 00:43:18.778963

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b398cde9d3e"
down_revision: Union[str, None] = "726412e8862d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("monitored_paths")}

    if "max_concurrent_migrations" not in column_names:
        op.add_column(
            "monitored_paths",
            sa.Column(
                "max_concurrent_migrations", sa.Integer(), nullable=False, server_default="3"
            ),
        )

    check_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("monitored_paths")
    }
    if "chk_max_concurrent_migrations_positive" not in check_constraints:
        # SQLite requires using batch_alter_table for constraints in some modes.
        with op.batch_alter_table("monitored_paths") as batch_op:
            batch_op.create_check_constraint(
                "chk_max_concurrent_migrations_positive",
                "max_concurrent_migrations >= 1"
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    check_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("monitored_paths")
    }
    column_names = {column["name"] for column in inspector.get_columns("monitored_paths")}

    with op.batch_alter_table("monitored_paths") as batch_op:
        if "chk_max_concurrent_migrations_positive" in check_constraints:
            batch_op.drop_constraint("chk_max_concurrent_migrations_positive", type_="check")
        if "max_concurrent_migrations" in column_names:
            batch_op.drop_column("max_concurrent_migrations")
