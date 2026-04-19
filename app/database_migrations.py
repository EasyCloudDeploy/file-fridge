"""Database migration utilities."""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

INITIAL_REVISION = "726412e8862d"
MAX_CONCURRENT_MIGRATIONS_REVISION = "6b398cde9d3e"
RELOCATION_TASK_REVISION = "4cb41a7faab6"
PERMISSIONS_ERROR_REVISION = "764abe6a5a03"
HEAD_REVISION = "a1b2c3d4e5f6"
BACKEND_MODULES_REVISION = "9f3d6e2aa1b1"
LOCAL_DRIVE_IDENTITY_REVISION = "b17d9f43c2aa"
ALLOW_OFFLINE_REVISION = "c3e1d8f7aa42"
NORMALIZE_COLD_STORAGE_ENUM_VALUES_REVISION = "d4f9b8a1c2e3"


def _table_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _schema_has_head_markers(inspector) -> bool:
    """Return True when schema already matches the current head structure."""
    tables = set(inspector.get_table_names())

    required_tables = {"remote_audit_logs", "remote_connection_path_permissions"}
    if not required_tables.issubset(tables):
        return False

    if "remote_connections" not in tables or "remote_transfer_jobs" not in tables:
        return False

    remote_connection_columns = _table_columns(inspector, "remote_connections")
    transfer_job_columns = _table_columns(inspector, "remote_transfer_jobs")

    return {"last_seen_at", "is_reachable"}.issubset(remote_connection_columns) and {
        "created_at",
        "updated_at",
    }.issubset(transfer_job_columns)


def _cold_storage_values_need_normalization(db) -> bool:
    """Return True when lowercase legacy enum values are still present."""
    result = db.execute(
        text(
            """
            SELECT 1
            FROM cold_storage_locations
            WHERE TRIM(backend_type) IN ('local', 's3', 'gdrive')
               OR TRIM(operation_mode) IN ('move', 'copy', 'symlink')
            LIMIT 1
            """
        )
    ).fetchone()
    return result is not None


def _determine_schema_revision(inspector, db) -> str:
    """Infer the closest Alembic revision from the live schema."""
    tables = set(inspector.get_table_names())

    if _schema_has_head_markers(inspector):
        return HEAD_REVISION

    if "monitored_paths" in tables:
        monitored_path_columns = _table_columns(inspector, "monitored_paths")
        if "permissions_error" in monitored_path_columns:
            if "cold_storage_locations" in tables:
                cold_storage_columns = _table_columns(inspector, "cold_storage_locations")
                if "allow_offline" in cold_storage_columns:
                    if _cold_storage_values_need_normalization(db):
                        return ALLOW_OFFLINE_REVISION
                    return NORMALIZE_COLD_STORAGE_ENUM_VALUES_REVISION
                if "local_drive_identifier" in cold_storage_columns:
                    return LOCAL_DRIVE_IDENTITY_REVISION
                if "backend_type" in cold_storage_columns:
                    return BACKEND_MODULES_REVISION
            return PERMISSIONS_ERROR_REVISION

    if "relocation_tasks" in tables:
        return RELOCATION_TASK_REVISION

    if "monitored_paths" in tables:
        monitored_path_columns = {
            column["name"] for column in inspector.get_columns("monitored_paths")
        }
        if "max_concurrent_migrations" in monitored_path_columns:
            return MAX_CONCURRENT_MIGRATIONS_REVISION

    return INITIAL_REVISION


def _create_sqlite_backup() -> Path | None:
    """Create a timestamped SQLite backup before running migrations."""
    if engine.url.get_backend_name() != "sqlite":
        logger.info("Skipping startup migration backup: non-SQLite database engine")
        return None

    database_file = engine.url.database
    if not database_file or database_file == ":memory:":
        logger.info("Skipping startup migration backup: in-memory SQLite database")
        return None

    source = Path(database_file)
    if not source.exists():
        logger.info("Skipping startup migration backup: database file does not exist yet")
        return None

    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{source.name}.pre_migration.{timestamp}.bak"

    source_uri = f"file:{source.absolute()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as src_conn:
        with sqlite3.connect(backup_path) as backup_conn:
            src_conn.backup(backup_conn)

    logger.info("Created pre-migration backup at %s", backup_path)
    return backup_path


def run_startup_migrations() -> None:
    """
    Run database migrations using Alembic on application startup.

    Handles the case where tables were created by init_db() but alembic_version
    is empty. In this case, we stamp the database with the current head before
    running migrations.
    """
    backup_path: Path | None = None
    try:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))

        # Check if we need to stamp the database
        # This happens when init_db() created tables but alembic_version is empty or missing
        db = SessionLocal()
        try:
            inspector = inspect(engine)
            tables = inspector.get_table_names()

            # Check for existing version
            has_alembic_table = "alembic_version" in tables
            has_version = False
            if has_alembic_table:
                result = db.execute(text("SELECT version_num FROM alembic_version")).fetchone()
                has_version = result is not None

            has_app_tables = len(tables) > (1 if has_alembic_table else 0)
            if has_app_tables:
                backup_path = _create_sqlite_backup()

            # If we have tables but no alembic version, determine the closest
            # revision from the live schema before upgrading further.
            if not has_version and has_app_tables:
                logger.info(
                    "Database tables exist but alembic version is not set. "
                    "Determining correct version based on schema..."
                )

                revision_to_stamp = _determine_schema_revision(inspector, db)
                logger.info("Detected schema equivalent to revision %s", revision_to_stamp)
                command.stamp(alembic_cfg, revision_to_stamp)

                logger.info("✓ Database stamped with appropriate version")
        finally:
            db.close()

        # Run Alembic upgrade to head (this will be a no-op if already at head)
        command.upgrade(alembic_cfg, "head")
        logger.info("✓ Database migrations completed successfully")
    except Exception as e:
        logger.exception("Failed to run startup migrations", exc_info=e)
        recovery_message = (
            "Startup migrations failed; aborting startup. "
            "After investigating, you can rollback recent migrations with "
            "`uv run alembic downgrade -3`."
        )
        if backup_path is not None:
            recovery_message = (
                f"{recovery_message} Latest automatic backup: {backup_path}."
            )
        raise RuntimeError(recovery_message) from e
