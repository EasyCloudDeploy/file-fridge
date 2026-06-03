import sqlite3
import importlib.util
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.script import ScriptDirectory
from app.database_migrations import run_startup_migrations

alembic_cfg = Config("alembic.ini")
script = ScriptDirectory.from_config(alembic_cfg)
HEAD_REVISION = script.get_current_head()

SNAPSHOT_DIR = Path("tests/fixtures/db_snapshots")


def _load_a1_migration_module():
    migration_path = (
        Path("alembic/versions/a1b2c3d4e5f6_add_remote_connection_health_and_audit.py")
        .absolute()
    )
    spec = importlib.util.spec_from_file_location("a1_migration_module", migration_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load migration module from {migration_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_migrations_up_and_down(alembic_config):
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

        tables = inspector.get_table_names()
        assert "relocation_tasks" in tables

        # Verify the default value for max_concurrent_migrations
        columns = inspector.get_columns("monitored_paths")
        for col in columns:
            if col["name"] == "max_concurrent_migrations":
                default_val = col["default"]
                if default_val is not None:
                    assert default_val.strip("'\"") == "3"

        # The P2P v2 migration (e6f7a8b9c0d1) is intentionally irreversible.
        # Verify it raises NotImplementedError rather than silently failing.
        with pytest.raises(NotImplementedError, match="hard cutover"):
            command.downgrade(cfg, "a1b2c3d4e5f6")


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
        assert "permissions_error" in [col["name"] for col in inspector.get_columns("monitored_paths")]
        assert "permissions_error" in [col["name"] for col in inspector.get_columns("cold_storage_locations")]


def test_cold_storage_backend_values_normalize_on_upgrade(alembic_config):
    """Upgrade should normalize lowercase backend_type/operation_mode values."""
    cfg, db_url = alembic_config

    with patch("app.config.Settings.database_url", new_callable=PropertyMock, return_value=db_url):
        engine = sa.create_engine(db_url)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                CREATE TABLE cold_storage_locations (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    path VARCHAR NOT NULL,
                    caution_threshold_percent INTEGER NOT NULL DEFAULT 20,
                    critical_threshold_percent INTEGER NOT NULL DEFAULT 10,
                    is_encrypted BOOLEAN NOT NULL DEFAULT 0,
                    encryption_status VARCHAR(16) NOT NULL DEFAULT 'none',
                    backend_type VARCHAR(16) NOT NULL DEFAULT 'local',
                    operation_mode VARCHAR(16) NOT NULL DEFAULT 'move',
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """
                )
            )
            connection.execute(
                sa.text(
                    """
                INSERT INTO cold_storage_locations (
                    id, name, path, caution_threshold_percent, critical_threshold_percent,
                    is_encrypted, encryption_status, backend_type, operation_mode
                ) VALUES
                    (1, 'legacy-lower', '/tmp/legacy-lower', 20, 10, 0, 'none', 'local', 'move'),
                    (2, 'already-upper', '/tmp/already-upper', 20, 10, 0, 'none', 'S3', 'COPY')
            """
                )
            )

        command.stamp(cfg, "c3e1d8f7aa42")
        command.upgrade(cfg, "head")

        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    """
                    SELECT id, backend_type, operation_mode
                    FROM cold_storage_locations
                    ORDER BY id
                    """
                )
            ).fetchall()

        assert rows[0] == (1, "LOCAL", "MOVE")
        assert rows[1] == (2, "S3", "COPY")


def test_remote_audit_and_permissions_revision_downgrades_cleanly(alembic_config):
    """Upgrade to a1b2..., then downgrade back to d4f9... and verify schema rollback."""
    cfg, db_url = alembic_config
    rev_a1 = _load_a1_migration_module()

    with patch("app.config.Settings.database_url", new_callable=PropertyMock, return_value=db_url):
        engine = sa.create_engine(db_url)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    CREATE TABLE monitored_paths (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                sa.text(
                    """
                    CREATE TABLE remote_connections (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        url VARCHAR NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                sa.text(
                    """
                    CREATE TABLE remote_transfer_jobs (
                        id INTEGER PRIMARY KEY,
                        remote_connection_id INTEGER NOT NULL
                    )
                    """
                )
            )

        command.stamp(cfg, "d4f9b8a1c2e3")
        command.upgrade(cfg, "a1b2c3d4e5f6")

        with engine.connect() as connection:
            with patch.object(rev_a1.op, "get_bind", return_value=connection):
                assert rev_a1._table_exists("remote_connection_path_permissions")
                assert rev_a1._table_exists("remote_audit_logs")
                assert rev_a1._column_exists("remote_transfer_jobs", "created_at")
                assert rev_a1._column_exists("remote_transfer_jobs", "updated_at")
                assert rev_a1._column_exists("remote_connections", "is_reachable")
                assert rev_a1._column_exists("remote_connections", "last_seen_at")

        command.downgrade(cfg, "d4f9b8a1c2e3")

        with engine.connect() as connection:
            with patch.object(rev_a1.op, "get_bind", return_value=connection):
                assert not rev_a1._table_exists("remote_connection_path_permissions")
                assert not rev_a1._table_exists("remote_audit_logs")
                assert not rev_a1._column_exists("remote_transfer_jobs", "created_at")
                assert not rev_a1._column_exists("remote_transfer_jobs", "updated_at")
                assert not rev_a1._column_exists("remote_connections", "is_reachable")
                assert not rev_a1._column_exists("remote_connections", "last_seen_at")

            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == "d4f9b8a1c2e3"


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
def test_run_startup_migrations_against_real_world_snapshots(
    tmp_path, snapshot_name
):
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

    with engine.connect() as connection:
        version = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert version == HEAD_REVISION
