import sqlite3
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.database_migrations import HEAD_REVISION, run_startup_migrations

SNAPSHOT_DIR = Path("tests/fixtures/db_snapshots")


@pytest.fixture
def alembic_config(tmp_path):
    """Provides an Alembic Config object pointed to a temporary SQLite database."""
    ini_path = Path("alembic.ini").absolute()
    cfg = Config(str(ini_path))

    script_location = Path("alembic").absolute()
    cfg.set_main_option("script_location", str(script_location))

    # Use file-based SQLite database so the schema persists across connections
    db_path = tmp_path / "test_migrations.db"
    db_url = f"sqlite:///{db_path}"

    return cfg, db_url


def test_migrations_up_and_down(alembic_config):  # noqa: PLR0915
    """
    Test that Alembic can successfully upgrade to head and downgrade back to base.
    This ensures that all migrations have valid syntax and correct rollback logic.
    """
    cfg, db_url = alembic_config

    # Alembic's env.py forces settings.database_url (which is a property),
    # so we must mock it out by using PropertyMock on the class itself.
    with patch("app.config.Settings.database_url", new_callable=PropertyMock, return_value=db_url):
        engine = sa.create_engine(db_url)

        # Create the old 'monitored_paths' table explicitly using raw SQL.
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                CREATE TABLE monitored_paths (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR2(255) NOT NULL,
                    source_path VARCHAR2(1024) NOT NULL,
                    operation_type VARCHAR2(50),
                    check_interval_seconds INTEGER,
                    enabled BOOLEAN,
                    prevent_indexing BOOLEAN NOT NULL,
                    error_message TEXT,
                    last_scan_at DATETIME,
                    last_scan_status VARCHAR2(50),
                    last_scan_error_log TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """
                )
            )
            connection.execute(
                sa.text(
                    """
                CREATE TABLE cold_storage_locations (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR2(255) NOT NULL,
                    path VARCHAR2(1024) NOT NULL,
                    caution_threshold_percent INTEGER NOT NULL,
                    critical_threshold_percent INTEGER NOT NULL,
                    is_encrypted BOOLEAN NOT NULL,
                    encryption_status VARCHAR(10) NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """
                )
            )
            # Add file_inventory for relocation_tasks foreign key
            connection.execute(
                sa.text(
                    """
                CREATE TABLE file_inventory (
                    id INTEGER PRIMARY KEY,
                    path_id INTEGER NOT NULL,
                    file_path VARCHAR NOT NULL,
                    storage_type VARCHAR NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_mtime DATETIME NOT NULL,
                    is_encrypted BOOLEAN NOT NULL
                )
            """
                )
            )

        # Apply initial empty migration explicitly or stamp it
        command.stamp(cfg, "726412e8862d")

        # Upgrade to head
        command.upgrade(cfg, "head")

        # Verify the tables and columns exist
        inspector = sa.inspect(engine)

        columns = inspector.get_columns("monitored_paths")
        column_names = [col["name"] for col in columns]
        assert "max_concurrent_migrations" in column_names
        assert "permissions_error" in column_names

        columns = inspector.get_columns("cold_storage_locations")
        column_names = [col["name"] for col in columns]
        assert "permissions_error" in column_names

        # New columns from recent migrations
        assert "backend_type" in column_names
        assert "operation_mode" in column_names
        assert "backend_config_encrypted" in column_names
        assert "local_drive_identifier" in column_names
        assert "local_drive_label" in column_names
        assert "local_drive_mount_path" in column_names
        assert "local_drive_is_removable" in column_names
        assert "local_drive_is_connected" in column_names
        assert "local_drive_last_seen_at" in column_names
        assert "allow_offline" in column_names

        tables = inspector.get_table_names()
        assert "relocation_tasks" in tables

        # Verify the default value for max_concurrent_migrations
        columns = inspector.get_columns("monitored_paths")
        for col in columns:
            if col["name"] == "max_concurrent_migrations":
                default_val = col["default"]
                if default_val is not None:
                    assert default_val.strip("'\"") == "3"

        # Downgrade back to base
        command.downgrade(cfg, "726412e8862d")

        # Verify columns and tables are gone
        inspector = sa.inspect(engine)
        columns = inspector.get_columns("monitored_paths")
        column_names = [col["name"] for col in columns]
        assert "max_concurrent_migrations" not in column_names
        assert "permissions_error" not in column_names

        columns = inspector.get_columns("cold_storage_locations")
        column_names = [col["name"] for col in columns]
        assert "permissions_error" not in column_names

        # New columns from recent migrations should be removed
        assert "backend_type" not in column_names
        assert "operation_mode" not in column_names
        assert "backend_config_encrypted" not in column_names
        assert "local_drive_identifier" not in column_names
        assert "local_drive_label" not in column_names
        assert "local_drive_mount_path" not in column_names
        assert "local_drive_is_removable" not in column_names
        assert "local_drive_is_connected" not in column_names
        assert "local_drive_last_seen_at" not in column_names
        assert "allow_offline" not in column_names

        tables = inspector.get_table_names()
        assert "relocation_tasks" not in tables


def test_permissions_error_upgrade_is_idempotent(alembic_config):
    """Upgrade should succeed even if permissions_error already exists (drifted schema)."""
    cfg, db_url = alembic_config

    with patch("app.config.Settings.database_url", new_callable=PropertyMock, return_value=db_url):
        engine = sa.create_engine(db_url)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                CREATE TABLE monitored_paths (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    source_path VARCHAR NOT NULL,
                    prevent_indexing BOOLEAN NOT NULL,
                    permissions_error TEXT,
                    max_concurrent_migrations INTEGER NOT NULL DEFAULT 3
                )
            """
                )
            )
            connection.execute(
                sa.text(
                    """
                CREATE TABLE cold_storage_locations (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    path VARCHAR NOT NULL,
                    caution_threshold_percent INTEGER NOT NULL,
                    critical_threshold_percent INTEGER NOT NULL,
                    is_encrypted BOOLEAN NOT NULL,
                    encryption_status VARCHAR(10) NOT NULL,
                    permissions_error TEXT
                )
            """
                )
            )
            # Add file_inventory for relocation_tasks foreign key
            connection.execute(
                sa.text(
                    """
                CREATE TABLE file_inventory (
                    id INTEGER PRIMARY KEY,
                    path_id INTEGER NOT NULL,
                    file_path VARCHAR NOT NULL,
                    storage_type VARCHAR NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_mtime DATETIME NOT NULL,
                    is_encrypted BOOLEAN NOT NULL
                )
            """
                )
            )

        command.stamp(cfg, "726412e8862d")
        command.upgrade(cfg, "head")

        inspector = sa.inspect(engine)
        assert "permissions_error" in [
            col["name"] for col in inspector.get_columns("monitored_paths")
        ]
        assert "permissions_error" in [
            col["name"] for col in inspector.get_columns("cold_storage_locations")
        ]


def _restore_snapshot(snapshot_name: str, tmp_path: Path) -> tuple[sa.Engine, str]:
    """Restore a persisted SQL snapshot into a temporary SQLite database."""
    snapshot_path = SNAPSHOT_DIR / snapshot_name
    db_path = tmp_path / f"{snapshot_path.stem}.db"

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(snapshot_path.read_text())
        connection.commit()
    finally:
        connection.close()

    db_url = f"sqlite:///{db_path}"
    return sa.create_engine(db_url), db_url


@pytest.mark.parametrize("snapshot_name", ["pre_max_concurrent.sql", "drifted_max_concurrent.sql"])
def test_run_startup_migrations_against_real_world_snapshots(tmp_path, snapshot_name):
    """Upgrade persisted legacy snapshots all the way to head without errors."""
    engine, db_url = _restore_snapshot(snapshot_name, tmp_path)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with (
        patch("app.config.Settings.database_url", new_callable=PropertyMock, return_value=db_url),
        patch("app.database_migrations.engine", engine),
        patch("app.database_migrations.SessionLocal", SessionLocal),
    ):
        run_startup_migrations()

    inspector = sa.inspect(engine)
    monitored_path_columns = [col["name"] for col in inspector.get_columns("monitored_paths")]
    assert "max_concurrent_migrations" in monitored_path_columns
    assert "permissions_error" in monitored_path_columns

    if "cold_storage_locations" in inspector.get_table_names():
        cold_storage_columns = [
            col["name"] for col in inspector.get_columns("cold_storage_locations")
        ]
        assert "permissions_error" in cold_storage_columns
        assert "backend_type" in cold_storage_columns
        assert "operation_mode" in cold_storage_columns
        assert "backend_config_encrypted" in cold_storage_columns
        assert "local_drive_identifier" in cold_storage_columns
        assert "local_drive_label" in cold_storage_columns
        assert "local_drive_mount_path" in cold_storage_columns
        assert "local_drive_is_removable" in cold_storage_columns
        assert "local_drive_is_connected" in cold_storage_columns
        assert "local_drive_last_seen_at" in cold_storage_columns
        assert "allow_offline" in cold_storage_columns

    with engine.connect() as connection:
        version = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert version == HEAD_REVISION
