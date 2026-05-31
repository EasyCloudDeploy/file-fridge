"""Add psk_encrypted to p2p_network_config

Revision ID: f1a2b3c4d5e6
Revises: 099b10f2eff5
Create Date: 2026-05-31 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "099b10f2eff5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "p2p_network_config" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("p2p_network_config")}

    with op.batch_alter_table("p2p_network_config") as batch_op:
        if "psk_encrypted" not in columns:
            batch_op.add_column(
                sa.Column(
                    "psk_encrypted",
                    sa.Text(),
                    nullable=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "p2p_network_config" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("p2p_network_config")}

    with op.batch_alter_table("p2p_network_config") as batch_op:
        if "psk_encrypted" in columns:
            batch_op.drop_column("psk_encrypted")
