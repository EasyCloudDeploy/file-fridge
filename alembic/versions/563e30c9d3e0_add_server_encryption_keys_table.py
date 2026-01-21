"""add server encryption keys table

Revision ID: 563e30c9d3e0
Revises: ab498f37013c
Create Date: 2026-01-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import os
import hashlib
from cryptography.fernet import Fernet


# revision identifiers, used by Alembic.
revision = '563e30c9d3e0'
down_revision = 'ab498f37013c'
branch_labels = None
depends_on = None


def upgrade():
    # Create server_encryption_keys table
    op.create_table(
        'server_encryption_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key_value', sa.String(), nullable=False),
        sa.Column('fingerprint', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_server_encryption_keys_fingerprint'), 'server_encryption_keys', ['fingerprint'], unique=True)
    op.create_index(op.f('ix_server_encryption_keys_id'), 'server_encryption_keys', ['id'], unique=False)

    # Migration of existing key file
    try:
        # We need to access settings but we don't want to import app.config if possible
        # to avoid side effects. Use default path.
        key_file = 'data/encryption.key'
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                key_value = f.read().decode().strip()

            if key_value:
                fingerprint = hashlib.sha256(key_value.encode()).hexdigest()

                # Use a connection to insert the existing key
                bind = op.get_bind()
                session = sa.orm.Session(bind=bind)

                # Check if it already exists (unlikely in fresh migration but good for idempotency)
                existing = session.execute(
                    sa.text("SELECT id FROM server_encryption_keys WHERE fingerprint = :fp"),
                    {"fp": fingerprint}
                ).fetchone()

                if not existing:
                    session.execute(
                        sa.text("INSERT INTO server_encryption_keys (key_value, fingerprint) VALUES (:val, :fp)"),
                        {"val": key_value, "fp": fingerprint}
                    )
                    session.commit()
    except Exception as e:
        print(f"Warning: Failed to migrate existing encryption key: {e}")


def downgrade():
    op.drop_index(op.f('ix_server_encryption_keys_id'), table_name='server_encryption_keys')
    op.drop_index(op.f('ix_server_encryption_keys_fingerprint'), table_name='server_encryption_keys')
    op.drop_table('server_encryption_keys')
