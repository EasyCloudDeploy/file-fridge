"""Additional database migration utility tests."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import inspect, text

from app.database import Base, engine, init_db
from app.database_migrations import (
    BACKEND_MODULES_REVISION,
    INITIAL_REVISION,
    LOCAL_DRIVE_IDENTITY_REVISION,
    PERMISSIONS_ERROR_REVISION,
    RELOCATION_TASK_REVISION,
    _cold_storage_values_need_normalization,
    _ensure_post_migration_schema,
    _prune_backups,
    _schema_has_p2p_v2_markers,
    _table_columns,
    run_startup_migrations,
)


@pytest.fixture(autouse=True)
def clean_database_after_each_test(db_session):
    Base.metadata.drop_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.mark.unit
def test_table_columns_helper():
    init_db()
    inspector = inspect(engine)
    columns = _table_columns(inspector, "monitored_paths")
    assert "name" in columns
    assert "source_path" in columns


@pytest.mark.unit
def test_schema_has_p2p_v2_markers_false_without_tables():
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["monitored_paths"]
    assert _schema_has_p2p_v2_markers(inspector) is False


@pytest.mark.unit
def test_schema_has_p2p_v2_markers_true():
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "file_inventory",
        "p2p_network_config",
        "p2p_peers",
        "remote_shared_file_cache",
    ]

    def get_columns(table_name):
        if table_name == "file_inventory":
            return [{"name": "is_shareable"}]
        return [{"name": "id"}]

    inspector.get_columns.side_effect = get_columns
    assert _schema_has_p2p_v2_markers(inspector) is True


@pytest.mark.unit
def test_cold_storage_values_need_normalization(db_session):
    from app.models import ColdStorageBackendType, ColdStorageLocation, OperationType

    init_db()
    legacy = ColdStorageLocation(
        name="Legacy",
        path="/tmp/cold",
        backend_type=ColdStorageBackendType.LOCAL,
        operation_mode=OperationType.MOVE,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.execute(
        text("""
            UPDATE cold_storage_locations
            SET backend_type = 'local', operation_mode = 'move'
            WHERE id = :location_id
            """),
        {"location_id": legacy.id},
    )
    db_session.commit()
    assert _cold_storage_values_need_normalization(db_session) is True


@pytest.mark.unit
def test_determine_schema_revision_relocation_task():
    from app.database_migrations import _determine_schema_revision

    inspector = MagicMock()
    inspector.get_table_names.return_value = ["relocation_tasks", "monitored_paths"]
    db = MagicMock()
    revision = _determine_schema_revision(inspector, db)
    assert revision == RELOCATION_TASK_REVISION


@pytest.mark.unit
def test_determine_schema_revision_permissions_error():
    from app.database_migrations import _determine_schema_revision

    inspector = MagicMock()
    inspector.get_table_names.return_value = ["monitored_paths", "cold_storage_locations"]

    def get_columns(table_name):
        if table_name == "monitored_paths":
            return [{"name": "permissions_error"}]
        if table_name == "cold_storage_locations":
            return [{"name": "path"}]
        return [{"name": "id"}]

    inspector.get_columns.side_effect = get_columns
    db = MagicMock()
    assert _determine_schema_revision(inspector, db) == PERMISSIONS_ERROR_REVISION


@pytest.mark.unit
def test_determine_schema_revision_backend_modules():
    from app.database_migrations import _determine_schema_revision

    inspector = MagicMock()
    inspector.get_table_names.return_value = ["monitored_paths", "cold_storage_locations"]

    def get_columns(table_name):
        if table_name == "monitored_paths":
            return [{"name": "permissions_error"}]
        if table_name == "cold_storage_locations":
            return [{"name": "backend_type"}]
        return [{"name": "id"}]

    inspector.get_columns.side_effect = get_columns
    db = MagicMock()
    assert _determine_schema_revision(inspector, db) == BACKEND_MODULES_REVISION


@pytest.mark.unit
def test_determine_schema_revision_local_drive_identity():
    from app.database_migrations import _determine_schema_revision

    inspector = MagicMock()
    inspector.get_table_names.return_value = ["monitored_paths", "cold_storage_locations"]

    def get_columns(table_name):
        if table_name == "monitored_paths":
            return [{"name": "permissions_error"}]
        if table_name == "cold_storage_locations":
            return [{"name": "local_drive_identifier"}]
        return [{"name": "id"}]

    inspector.get_columns.side_effect = get_columns
    db = MagicMock()
    assert _determine_schema_revision(inspector, db) == LOCAL_DRIVE_IDENTITY_REVISION


@pytest.mark.unit
def test_determine_schema_revision_initial():
    from app.database_migrations import _determine_schema_revision

    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    db = MagicMock()
    assert _determine_schema_revision(inspector, db) == INITIAL_REVISION


@pytest.mark.unit
def test_prune_backups_keeps_recent_only(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    paths = []
    for idx in range(12):
        backup = backup_dir / f"file_fridge.db.pre_migration.2025010{idx}T000000Z.bak"
        backup.write_text(f"backup-{idx}", encoding="utf-8")
        paths.append(backup)

    _prune_backups(backup_dir=backup_dir, database_filename="file_fridge.db")
    remaining = list(backup_dir.glob("*.bak"))
    assert len(remaining) == 10


@pytest.mark.unit
def test_ensure_post_migration_schema_adds_is_shareable(monkeypatch: pytest.MonkeyPatch):
    mock_inspector = MagicMock()
    mock_inspector.get_table_names.return_value = ["file_inventory"]
    mock_inspector.get_columns.return_value = [{"name": "id"}, {"name": "file_path"}]

    mock_connection = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__ = MagicMock(return_value=mock_connection)
    mock_context.__exit__ = MagicMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.dialect.name = "sqlite"
    mock_engine.begin.return_value = mock_context

    with patch("app.database_migrations.engine", mock_engine):
        with patch("app.database_migrations.inspect", return_value=mock_inspector):
            _ensure_post_migration_schema()

    assert mock_connection.execute.call_count == 2


@pytest.mark.unit
def test_ensure_post_migration_schema_skips_non_sqlite(monkeypatch: pytest.MonkeyPatch):
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    with patch("app.database_migrations.engine", mock_engine):
        _ensure_post_migration_schema()
    mock_engine.begin.assert_not_called()


@pytest.mark.unit
@patch("alembic.command.upgrade", side_effect=RuntimeError("Pre-migration backup failed: disk"))
def test_run_startup_migrations_reraises_backup_failure(mock_upgrade, monkeypatch):
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")
    with pytest.raises(RuntimeError, match="Pre-migration backup failed"):
        run_startup_migrations()


@pytest.mark.unit
@patch("alembic.command.upgrade")
@patch("alembic.command.stamp")
@patch("app.database_migrations._create_sqlite_backup", return_value=Path("/tmp/backup.bak"))
def test_run_startup_migrations_includes_backup_in_error_message(
    mock_backup, mock_stamp, mock_upgrade, monkeypatch
):
    mock_upgrade.side_effect = RuntimeError("migration broke")
    monkeypatch.setattr("app.config.settings.database_path", ":memory:")

    Base.metadata.create_all(bind=engine)
    db = __import__("app.database", fromlist=["SessionLocal"]).SessionLocal()
    try:
        inspector = inspect(engine)
        if "alembic_version" not in inspector.get_table_names():
            with engine.begin() as conn:
                conn.execute(
                    text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                )
    finally:
        db.close()

    with pytest.raises(RuntimeError, match="Latest automatic backup"):
        run_startup_migrations()
