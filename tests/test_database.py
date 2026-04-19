from unittest.mock import ANY, MagicMock, patch

import pytest
from sqlalchemy import inspect

from app.database import Base, engine, has_schema_objects, init_db
from app.database_migrations import (
    ALLOW_OFFLINE_REVISION,
    HEAD_REVISION,
    MAX_CONCURRENT_MIGRATIONS_REVISION,
    NORMALIZE_COLD_STORAGE_ENUM_VALUES_REVISION,
    _determine_schema_revision,
    run_startup_migrations,
)


@pytest.fixture(autouse=True)
def clean_database_after_each_test(db_session):
    """Ensure a clean database for each test."""
    Base.metadata.drop_all(bind=engine)  # start each test with no tables
    yield
    Base.metadata.drop_all(bind=engine)


def test_init_db_creates_all_tables(db_session):
    """Test that init_db creates all defined tables."""
    # Alembic creates alembic_version on db creation, so there might be 1 table.
    assert len([t for t in inspect(engine).get_table_names() if t != "alembic_version"]) == 0

    init_db()

    # Use a fresh inspector — SQLAlchemy 2.0 caches results per Inspector instance
    table_names = inspect(engine).get_table_names()

    # Assert that some expected tables exist (not exhaustive, but representative)
    assert "monitored_paths" in table_names
    assert "file_inventory" in table_names
    assert "users" in table_names


def test_init_db_is_idempotent(db_session):
    """Test that calling init_db multiple times does not cause errors."""
    init_db()
    table_names_first_call = inspect(engine).get_table_names()

    init_db()
    table_names_second_call = inspect(engine).get_table_names()

    assert table_names_first_call == table_names_second_call


def test_has_schema_objects_false_for_empty_db(db_session):
    """Empty databases should not be treated as pre-existing application schemas."""
    assert has_schema_objects() is False


def test_has_schema_objects_true_when_tables_exist(db_session):
    """Existing application tables should be detected before startup migrations run."""
    init_db()
    assert has_schema_objects() is True


@patch("alembic.command.upgrade")
@patch("alembic.command.stamp")
def test_run_startup_migrations_empty_db(mock_stamp, mock_upgrade, db_session, monkeypatch):
    """Test migrations run on an empty database (no tables created by init_db)."""
    # Ensure no tables are present initially
    Base.metadata.drop_all(bind=engine)
    inspector = inspect(engine)
    assert len([t for t in inspector.get_table_names() if t != "alembic_version"]) == 0

    # Mock settings.database_path since alembic_cfg reads alembic.ini
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")

    run_startup_migrations()

    # upgrade should be called to head
    mock_upgrade.assert_called_once_with(ANY, "head")
    mock_stamp.assert_not_called()  # No stamping needed if no tables exist


@patch("alembic.command.upgrade")
@patch("alembic.command.stamp")
@patch("app.database_migrations._create_sqlite_backup")
@patch("app.database.engine")  # Patch the engine used by app.database to control inspect behavior
def test_run_startup_migrations_with_existing_tables_no_alembic_version(
    mock_engine, mock_create_backup, mock_stamp, mock_upgrade, db_session, monkeypatch
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

    # Needs to match all columns the migration check looks for
    mock_inspector.get_columns.return_value = [
        {"name": "instance_url"},
        {"name": "max_concurrent_migrations"},
    ]
    mock_engine.return_value.dialect.has_table.return_value = (
        True  # For alembic.util.exc.CommandError
    )

    with patch("app.database_migrations.inspect", return_value=mock_inspector):
        run_startup_migrations()

    # The max_concurrent_migrations column tells us this schema matches the
    # pre-head revision immediately before RelocationTask.
    mock_stamp.assert_called_once_with(ANY, MAX_CONCURRENT_MIGRATIONS_REVISION)
    mock_upgrade.assert_called_once_with(ANY, "head")
    mock_create_backup.assert_called_once()


@patch("alembic.command.upgrade")
@patch("alembic.command.stamp")
@patch("app.database_migrations._create_sqlite_backup")
@patch("app.database.engine")
def test_run_startup_migrations_stamps_max_concurrent_schema_to_matching_revision(
    mock_engine, mock_create_backup, mock_stamp, mock_upgrade, db_session, monkeypatch
):
    """Databases with the column already present should not be re-migrated from the base revision."""
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")

    mock_inspector = MagicMock()
    mock_inspector.get_table_names.return_value = ["monitored_paths", "users"]

    def get_columns(table_name):
        if table_name == "monitored_paths":
            return [{"name": "id"}, {"name": "max_concurrent_migrations"}]
        return [{"name": "id"}]

    mock_inspector.get_columns.side_effect = get_columns
    mock_engine.return_value.dialect.has_table.return_value = True

    with patch("app.database_migrations.inspect", return_value=mock_inspector):
        run_startup_migrations()

    mock_stamp.assert_called_once_with(ANY, MAX_CONCURRENT_MIGRATIONS_REVISION)
    mock_upgrade.assert_called_once_with(ANY, "head")
    mock_create_backup.assert_called_once()


def test_determine_schema_revision_returns_head_for_full_head_schema():
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "monitored_paths",
        "remote_connections",
        "remote_transfer_jobs",
        "remote_audit_logs",
        "remote_connection_path_permissions",
    ]

    def get_columns(table_name):
        columns = {
            "remote_connections": [{"name": "last_seen_at"}, {"name": "is_reachable"}],
            "remote_transfer_jobs": [{"name": "created_at"}, {"name": "updated_at"}],
            "monitored_paths": [{"name": "permissions_error"}],
        }
        return columns.get(table_name, [{"name": "id"}])

    inspector.get_columns.side_effect = get_columns
    db = MagicMock()

    revision = _determine_schema_revision(inspector, db)

    assert revision == HEAD_REVISION
    db.execute.assert_not_called()


def test_determine_schema_revision_returns_allow_offline_when_normalization_needed():
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["monitored_paths", "cold_storage_locations"]

    def get_columns(table_name):
        if table_name == "monitored_paths":
            return [{"name": "permissions_error"}]
        if table_name == "cold_storage_locations":
            return [{"name": "allow_offline"}]
        return [{"name": "id"}]

    inspector.get_columns.side_effect = get_columns
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (1,)

    revision = _determine_schema_revision(inspector, db)

    assert revision == ALLOW_OFFLINE_REVISION


def test_determine_schema_revision_returns_normalized_revision_when_values_already_uppercase():
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["monitored_paths", "cold_storage_locations"]

    def get_columns(table_name):
        if table_name == "monitored_paths":
            return [{"name": "permissions_error"}]
        if table_name == "cold_storage_locations":
            return [{"name": "allow_offline"}]
        return [{"name": "id"}]

    inspector.get_columns.side_effect = get_columns
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = None

    revision = _determine_schema_revision(inspector, db)

    assert revision == NORMALIZE_COLD_STORAGE_ENUM_VALUES_REVISION


@patch("alembic.command.upgrade", side_effect=RuntimeError("boom"))
def test_run_startup_migrations_raises_on_failure(mock_upgrade, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")

    with pytest.raises(RuntimeError, match="Startup migrations failed; aborting startup"):
        run_startup_migrations()

    mock_upgrade.assert_called_once()
