from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


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
                    name VARCHAR NOT NULL,
                    source_path VARCHAR NOT NULL,
                    operation_type VARCHAR,
                    check_interval_seconds INTEGER,
                    enabled BOOLEAN,
                    prevent_indexing BOOLEAN NOT NULL,
                    error_message TEXT,
                    last_scan_at DATETIME,
                    last_scan_status VARCHAR,
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
