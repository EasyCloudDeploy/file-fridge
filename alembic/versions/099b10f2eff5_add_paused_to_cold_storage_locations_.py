"""Add paused to cold storage locations

Revision ID: 099b10f2eff5
Revises: e6f7a8b9c0d1
Create Date: 2026-05-31 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "099b10f2eff5"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
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
        if "paused" not in columns:
            batch_op.add_column(
                sa.Column(
                    "paused",
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
        if "paused" in columns:
            batch_op.drop_column("paused")
