"""add_previous_file_root_key_to_metadata

Revision ID: 40cf43c345b2
Revises: 7377f34fc6a3
Create Date: 2026-06-04 18:33:16.095538

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '40cf43c345b2'
down_revision: Union[str, None] = '7377f34fc6a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "instance_metadata" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("instance_metadata")}
    with op.batch_alter_table("instance_metadata") as batch_op:
        if "previous_file_encryption_root_key_encrypted" not in columns:
            batch_op.add_column(sa.Column('previous_file_encryption_root_key_encrypted', sa.Text(), nullable=True))
        if "file_migration_total" not in columns:
            batch_op.add_column(sa.Column('file_migration_total', sa.Integer(), nullable=True))
        if "file_migration_progress" not in columns:
            batch_op.add_column(sa.Column('file_migration_progress', sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "instance_metadata" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("instance_metadata")}
    with op.batch_alter_table("instance_metadata") as batch_op:
        if "file_migration_progress" in columns:
            batch_op.drop_column('file_migration_progress')
        if "file_migration_total" in columns:
            batch_op.drop_column('file_migration_total')
        if "previous_file_encryption_root_key_encrypted" in columns:
            batch_op.drop_column('previous_file_encryption_root_key_encrypted')
