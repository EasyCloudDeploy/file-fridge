"""add_instance_uuid and remote tables

Revision ID: d2a9627cb2b1
Revises: fix_schema_inconsistencies
Create Date: 2026-01-19 15:52:12.867344

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2a9627cb2b1"
down_revision: Union[str, None] = "fix_schema_inconsistencies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    # 1. Create remote_connections table if missing
    if "remote_connections" not in tables:
        op.create_table(
            "remote_connections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("shared_secret", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id")
        )
        op.create_index(op.f("ix_remote_connections_name"), "remote_connections", ["name"], unique=False)

    # 2. Create remote_transfer_jobs table if missing
    if "remote_transfer_jobs" not in tables:
        op.create_table(
            "remote_transfer_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("file_inventory_id", sa.Integer(), nullable=False),
            sa.Column("remote_connection_id", sa.Integer(), nullable=False),
            sa.Column("remote_monitored_path_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column("current_size", sa.Integer(), nullable=True),
            sa.Column("total_size", sa.Integer(), nullable=False),
            sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=True),
            sa.Column("source_path", sa.String(), nullable=False),
            sa.Column("relative_path", sa.String(), nullable=False),
            sa.Column("storage_type", sa.String(), nullable=False),
            sa.Column("checksum", sa.String(), nullable=True),
            sa.Column("current_speed", sa.Integer(), nullable=True),
            sa.Column("eta", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["file_inventory_id"], ["file_inventory.id"], ),
            sa.ForeignKeyConstraint(["remote_connection_id"], ["remote_connections.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id")
        )
        op.create_index(op.f("ix_remote_transfer_jobs_status"), "remote_transfer_jobs", ["status"], unique=False)

    # 3. Create instance_metadata table if missing
    if "instance_metadata" not in tables:
        op.create_table(
            "instance_metadata",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("instance_uuid", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("instance_uuid")
        )
        op.create_index(op.f("ix_instance_metadata_id"), "instance_metadata", ["id"], unique=False)

        # Generate and insert a UUID for this instance
        new_uuid = str(uuid.uuid4())
        op.execute(sa.text(f"INSERT INTO instance_metadata (instance_uuid) VALUES ('{new_uuid}')"))

    # 4. Add remote_instance_uuid to remote_connections if missing
    columns = [col["name"] for col in inspector.get_columns("remote_connections")]
    if "remote_instance_uuid" not in columns:
        with op.batch_alter_table("remote_connections", schema=None) as batch_op:
            batch_op.add_column(sa.Column("remote_instance_uuid", sa.String(), nullable=True))
            batch_op.create_index(batch_op.f("ix_remote_connections_remote_instance_uuid"), ["remote_instance_uuid"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("remote_connections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_remote_connections_remote_instance_uuid"))
        batch_op.drop_column("remote_instance_uuid")

    op.drop_index(op.f("ix_instance_metadata_id"), table_name="instance_metadata")
    op.drop_table("instance_metadata")

    op.drop_index(op.f("ix_remote_transfer_jobs_status"), table_name="remote_transfer_jobs")
    op.drop_table("remote_transfer_jobs")

    op.drop_index(op.f("ix_remote_connections_name"), table_name="remote_connections")
    op.drop_table("remote_connections")
