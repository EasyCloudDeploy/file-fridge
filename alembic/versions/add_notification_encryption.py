"""Add SMTP password encryption and composite indexes for notifications

Revision ID: add_notification_encryption
Revises: ee1a21b76bf2
Create Date: 2026-01-17 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_notification_encryption"
down_revision: Union[str, None] = "ee1a21b76bf2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add encryption field and indexes for notification system."""

    # 1. Add encrypted SMTP password column
    # First add new column (nullable for migration)
    op.add_column("notifiers", sa.Column("smtp_password_encrypted", sa.String(), nullable=True))

    # Copy and encrypt existing passwords
    conn = op.get_bind()
    # Note: Since we don't have encryption in migration context,
    # we'll migrate passwords as-is for now
    # The EncryptionManager will handle encryption on next write
    notifiers = conn.execute(
        sa.text("SELECT id, smtp_password FROM notifiers WHERE smtp_password IS NOT NULL")
    ).fetchall()

    for notifier_id, password in notifiers:
        if password:
            conn.execute(
                sa.text("UPDATE notifiers SET smtp_password_encrypted = :pwd WHERE id = :id"),
                {"pwd": password, "id": notifier_id},
            )

    # Drop old column
    op.drop_column("notifiers", "smtp_password")

    # 2. Rename encrypted column to original name (using SQLAlchemy property for encryption/decryption)
    # Note: We keep smtp_password_encrypted as the DB column name, but the model will expose it via property

    # 3. Add composite indexes for notification dispatches
    op.create_index(
        "idx_dispatch_notifier_status", "notification_dispatches", ["notifier_id", "status"]
    )

    op.create_index("idx_notification_level_created", "notifications", ["level", "created_at"])


def downgrade() -> None:
    """Reverse migration: remove encryption and indexes."""

    # 1. Drop composite indexes
    op.drop_index("idx_notification_level_created", "notifications")
    op.drop_index("idx_dispatch_notifier_status", "notification_dispatches")

    # 2. Restore original smtp_password column
    op.add_column("notifiers", sa.Column("smtp_password", sa.String(), nullable=True))

    # Copy back unencrypted (since we didn't encrypt during migration)
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE notifiers SET smtp_password = smtp_password_encrypted WHERE smtp_password_encrypted IS NOT NULL"
        )
    )

    # Drop encrypted column
    op.drop_column("notifiers", "smtp_password_encrypted")
