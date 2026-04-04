import sqlite3
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.database_migrations import run_startup_migrations

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


def test_migrations_up_and_down(alembic_config):
    """
    Test that Alembic can successfully upgrade to head and downgrade back to base.
    This ensures that all migrations (including the one adding max_concurrent_migrations)
    have valid syntax and correct rollback logic.
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

        # Apply initial empty migration explicitly or stamp it
        command.stamp(cfg, "726412e8862d")

        # Upgrade to head
        command.upgrade(cfg, "head")

        # Verify the table and the new column exist
        inspector = sa.inspect(engine)

        columns = inspector.get_columns("monitored_paths")
        column_names = [col["name"] for col in columns]
        assert "max_concurrent_migrations" in column_names

        # Verify the default value is correct
        for col in columns:
            if col["name"] == "max_concurrent_migrations":
                default_val = col["default"]
                if default_val is not None:
                    assert default_val.strip("'\"") == "3"

        # Downgrade back to base
        command.downgrade(cfg, "726412e8862d")

        # Verify the column is gone
        inspector = sa.inspect(engine)
        columns = inspector.get_columns("monitored_paths")
        column_names = [col["name"] for col in columns]
        assert "max_concurrent_migrations" not in column_names


def test_max_concurrent_migrations_upgrade_is_idempotent_for_drifted_schema(alembic_config):
    """Upgrade should succeed when the column already exists but Alembic is behind."""
    cfg, db_url = alembic_config

    with patch("app.config.Settings.database_url", new_callable=PropertyMock, return_value=db_url):
        engine = sa.create_engine(db_url)

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
                    max_concurrent_migrations INTEGER NOT NULL DEFAULT 3,
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

        command.stamp(cfg, "726412e8862d")
        command.upgrade(cfg, "head")

        inspector = sa.inspect(engine)
        column_names = [col["name"] for col in inspector.get_columns("monitored_paths")]
        assert "max_concurrent_migrations" in column_names


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

    with engine.connect() as connection:
        version = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert version == "4cb41a7faab6"
