"""Add bidirectional transfer support

Adds transfer_mode and remote_transfer_mode columns to remote_connections,
and direction column to remote_transfer_jobs, to support bidirectional
file transfers between instances.

Revision ID: add_bidirectional_transfer_support
Revises: add_instance_url_and_name
Create Date: 2026-01-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_bidirectional_transfer_support"
down_revision: Union[str, None] = "add_instance_url_and_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add transfer_mode columns to remote_connections
    try:
        op.add_column(
            "remote_connections",
            sa.Column(
                "transfer_mode",
                sa.Enum("PUSH_ONLY", "BIDIRECTIONAL", name="transfermode"),
                nullable=False,
                server_default=sa.text("'PUSH_ONLY'"),
            ),
        )
    except Exception:
        pass

    try:
        op.add_column(
            "remote_connections",
            sa.Column(
                "remote_transfer_mode",
                sa.Enum("PUSH_ONLY", "BIDIRECTIONAL", name="transfermode"),
                nullable=False,
                server_default=sa.text("'PUSH_ONLY'"),
            ),
        )
    except Exception:
        pass

    # Add direction column to remote_transfer_jobs
    try:
        op.add_column(
            "remote_transfer_jobs",
            sa.Column(
                "direction",
                sa.Enum("PUSH", "PULL", name="transferdirection"),
                nullable=False,
                server_default=sa.text("'PUSH'"),
            ),
        )
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_column("remote_transfer_jobs", "direction")
    except Exception:
        pass
    try:
        op.drop_column("remote_connections", "remote_transfer_mode")
    except Exception:
        pass
    try:
        op.drop_column("remote_connections", "transfer_mode")
    except Exception:
        pass
