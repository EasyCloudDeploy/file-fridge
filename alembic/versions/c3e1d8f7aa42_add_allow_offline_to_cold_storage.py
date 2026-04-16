"""Add allow_offline toggle to cold storage locations

Revision ID: c3e1d8f7aa42
Revises: b17d9f43c2aa
Create Date: 2026-04-16 10:25:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3e1d8f7aa42"
down_revision: Union[str, None] = "b17d9f43c2aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "cold_storage_locations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("cold_storage_locations")}

    with op.batch_alter_table("cold_storage_locations") as batch_op:
        if "allow_offline" not in columns:
            batch_op.add_column(
                sa.Column(
                    "allow_offline",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "cold_storage_locations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("cold_storage_locations")}

    with op.batch_alter_table("cold_storage_locations") as batch_op:
        if "allow_offline" in columns:
            batch_op.drop_column("allow_offline")
