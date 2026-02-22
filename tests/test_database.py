import os
from unittest.mock import ANY, MagicMock, patch

import pytest
from app.database import Base, engine, init_db
from app.database_migrations import run_startup_migrations
from sqlalchemy import inspect


@pytest.fixture(autouse=True)
def clean_database_after_each_test(db_session):
    """Ensure a clean database for each test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_init_db_creates_all_tables(db_session):
    """Test that init_db creates all defined tables."""
    inspector = inspect(engine)
    assert len(inspector.get_table_names()) == 0

    init_db()

    # Get table names after init_db
    table_names = inspector.get_table_names()

    # Assert that some expected tables exist (not exhaustive, but representative)
    assert "monitored_paths" in table_names
    assert "file_inventory" in table_names
    assert "users" in table_names
    assert "alembic_version" not in table_names  # init_db should not create alembic_version


def test_init_db_is_idempotent(db_session):
    """Test that calling init_db multiple times does not cause errors."""
    init_db()
    table_names_first_call = inspect(engine).get_table_names()

    init_db()
    table_names_second_call = inspect(engine).get_table_names()

    assert table_names_first_call == table_names_second_call


@patch("alembic.command.upgrade")
@patch("alembic.command.stamp")
def test_run_startup_migrations_empty_db(mock_stamp, mock_upgrade, db_session, monkeypatch):
    """Test migrations run on an empty database (no tables created by init_db)."""
    # Ensure no tables are present initially
    Base.metadata.drop_all(bind=engine)
    inspector = inspect(engine)
    assert len(inspector.get_table_names()) == 0

    # Mock settings.database_path since alembic_cfg reads alembic.ini
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")

    run_startup_migrations()

    # upgrade should be called to head
    mock_upgrade.assert_called_once_with(ANY, "head")
    mock_stamp.assert_not_called()  # No stamping needed if no tables exist


@patch("alembic.command.upgrade")
@patch("alembic.command.stamp")
@patch("app.database.engine")  # Patch the engine used by app.database to control inspect behavior
def test_run_startup_migrations_with_existing_tables_no_alembic_version(
    mock_engine, mock_stamp, mock_upgrade, db_session, monkeypatch
):
    """
    Test migrations when tables exist (from init_db) but alembic_version table is empty/missing.
    This simulates a fresh install where init_db runs first, then migrations.
    """
    # Simulate init_db creating some tables (but not alembic_version)
    Base.metadata.create_all(bind=engine)

    # Mock settings.database_path since alembic_cfg reads alembic.ini
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")

    # Mock inspector to simulate tables existing without alembic_version
    mock_inspector = MagicMock()
    mock_inspector.get_table_names.return_value = ["monitored_paths", "users", "instance_metadata"]
    mock_inspector.get_columns.return_value = [{"name": "instance_url"}]  # Simulate new columns
    mock_engine.return_value.dialect.has_table.return_value = (
        True  # For alembic.util.exc.CommandError
    )

    with patch("sqlalchemy.inspect", return_value=mock_inspector):
        run_startup_migrations()

    # Should stamp to head because instance_url column is present
    mock_stamp.assert_called_once_with(ANY, "head")
    mock_upgrade.assert_called_once_with(ANY, "head")
