"""Database migration utilities."""

import logging

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

INITIAL_REVISION = "726412e8862d"
MAX_CONCURRENT_MIGRATIONS_REVISION = "6b398cde9d3e"
RELOCATION_TASK_REVISION = "4cb41a7faab6"
HEAD_REVISION = "764abe6a5a03"


def _determine_schema_revision(inspector) -> str:
    """Infer the closest Alembic revision from the live schema."""
    tables = set(inspector.get_table_names())

    if "monitored_paths" in tables:
        monitored_path_columns = {
            column["name"] for column in inspector.get_columns("monitored_paths")
        }
        if "permissions_error" in monitored_path_columns:
            return HEAD_REVISION

    if "relocation_tasks" in tables:
        return RELOCATION_TASK_REVISION

    if "monitored_paths" in tables:
        monitored_path_columns = {
            column["name"] for column in inspector.get_columns("monitored_paths")
        }
        if "max_concurrent_migrations" in monitored_path_columns:
            return MAX_CONCURRENT_MIGRATIONS_REVISION

    return INITIAL_REVISION


def run_startup_migrations() -> None:
    """
    Run database migrations using Alembic on application startup.

    Handles the case where tables were created by init_db() but alembic_version
    is empty. In this case, we stamp the database with the current head before
    running migrations.
    """
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

            # If we have tables but no alembic version, determine the closest
            # revision from the live schema before upgrading further.
            if not has_version and len(tables) > (1 if has_alembic_table else 0):
                logger.info(
                    "Database tables exist but alembic version is not set. "
                    "Determining correct version based on schema..."
                )

                revision_to_stamp = _determine_schema_revision(inspector)
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
        # Don't crash the app, just log the error
        logger.warning("Application will continue, but some features may not work correctly")
