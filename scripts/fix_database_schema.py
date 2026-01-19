#!/usr/bin/env python3
"""Fix database schema inconsistencies for upgrading users.

This script handles databases that were created with an inconsistent schema.
Run this once after upgrading to the latest version.

Usage:
    uv run python scripts/fix_database_schema.py
"""

import sqlite3
from pathlib import Path


def fix_database_schema(db_path: str = "data/file_fridge.db"):
    """Fix database schema and alembic version tracking."""
    db_file = Path(db_path)

    if not db_file.exists():
        print(f"❌ Database not found: {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        print(f"🔧 Fixing database: {db_path}")

        # 1. Fix alembic_version if it points to non-existent revision
        cursor.execute("SELECT version_num FROM alembic_version")
        current_version = cursor.fetchone()

        if not current_version:
            print("❌ alembic_version table is empty")
            return False

        version_num = current_version[0]
        print(f"   Current alembic version: {version_num}")

        # List of valid migrations in order
        valid_versions = [
            "a_unified_migration",
            "3a6a9581dcf1",
            "1eab9db4e223",
            "fix_schema_inconsistencies",
        ]

        # Check if file_transaction_history table exists (created by a_unified_migration)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='file_transaction_history'"
        )
        has_file_transaction_history = cursor.fetchone() is not None

        # If version is invalid or if table exists but version is old, fix it
        if version_num not in valid_versions:
            print(f"   ⚠️  Invalid version '{version_num}', stamping to latest...")
            cursor.execute("UPDATE alembic_version SET version_num = ?", ("1eab9db4e223",))
            print("   ✅ Stamped as: 1eab9db4e223")
        elif version_num == "a_unified_migration" and has_file_transaction_history:
            print("   ⚠️  Database has tables from a_unified_migration but version is old")
            print("   ⚠️  Stamping to latest migration...")
            cursor.execute("UPDATE alembic_version SET version_num = ?", ("1eab9db4e223",))
            print("   ✅ Stamped as: 1eab9db4e223")
        else:
            print("   ✅ Version is valid")

        # 2. Check notifiers table for missing columns
        cursor.execute("PRAGMA table_info(notifiers)")
        columns = {row[1] for row in cursor.fetchall()}

        if "filter_level" not in columns:
            print("   ➕ Adding missing filter_level column...")
            cursor.execute(
                "ALTER TABLE notifiers ADD COLUMN filter_level VARCHAR(15) NOT NULL DEFAULT 'info'"
            )
            print("   ✅ Added filter_level column")

        # 3. Remove legacy subscribed_events column if it exists
        if "subscribed_events" in columns:
            print("   ➖ Removing legacy subscribed_events column...")
            try:
                cursor.execute("ALTER TABLE notifiers RENAME TO notifiers_old")
                cursor.execute(
                    """CREATE TABLE notifiers (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        type VARCHAR(15) NOT NULL,
                        address VARCHAR NOT NULL,
                        enabled BOOLEAN NOT NULL,
                        filter_level VARCHAR(15) NOT NULL DEFAULT 'info',
                        smtp_host VARCHAR,
                        smtp_port INTEGER,
                        smtp_user VARCHAR,
                        smtp_password VARCHAR,
                        smtp_sender VARCHAR,
                        smtp_use_tls BOOLEAN,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME
                    )"""
                )
                cursor.execute(
                    """INSERT INTO notifiers (
                        id, name, type, address, enabled, filter_level,
                        smtp_host, smtp_port, smtp_user, smtp_password, smtp_sender, smtp_use_tls,
                        created_at, updated_at
                    ) SELECT
                        id, name, type, address, enabled, filter_level,
                        smtp_host, smtp_port, smtp_user, smtp_password, smtp_sender, smtp_use_tls,
                        created_at, updated_at
                    FROM notifiers_old"""
                )
                cursor.execute("DROP TABLE notifiers_old")
                print("   ✅ Removed subscribed_events column")
            except Exception as e:
                print(f"   ⚠️  Could not remove subscribed_events column: {e}")
                print("   ⚠️  This column may not cause issues if it still exists")

        conn.commit()
        print("\n✅ Database schema fixed successfully!")
        print("\nNext steps:")
        print("   1. Run migrations: uv run alembic upgrade head")
        print("   2. Restart the application")
        return True

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Error fixing database: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    db_arg = sys.argv[1] if len(sys.argv) > 1 else "data/file_fridge.db"
    success = fix_database_schema(db_arg)
    sys.exit(0 if success else 1)
