"""add_scheme_to_p2p_peers

Revision ID: 5311846495bd
Revises: 40cf43c345b2
Create Date: 2026-06-04 18:52:40.489610

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5311846495bd'
down_revision: Union[str, None] = '40cf43c345b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "p2p_peers" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("p2p_peers")}
    with op.batch_alter_table("p2p_peers") as batch_op:
        if "scheme" not in columns:
            batch_op.add_column(sa.Column('scheme', sa.String(), nullable=False, server_default='http'))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "p2p_peers" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("p2p_peers")}
    with op.batch_alter_table("p2p_peers") as batch_op:
        if "scheme" in columns:
            batch_op.drop_column('scheme')
