"""Database migration utilities."""

import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.database import engine, SessionLocal

logger = logging.getLogger(__name__)


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
        # This happens when init_db() created tables but alembic_version is empty
        db = SessionLocal()
        try:
            inspector = inspect(engine)
            tables = inspector.get_table_names()

            # Check if alembic_version table exists and is empty
            if "alembic_version" in tables:
                result = db.execute(text("SELECT version_num FROM alembic_version")).fetchone()
                has_version = result is not None

                # If we have tables but no alembic version, stamp to current head
                if not has_version and len(tables) > 1:  # More than just alembic_version
                    logger.info(
                        "Database tables exist but alembic version is not set. "
                        "Stamping database with current migration state..."
                    )
                    command.stamp(alembic_cfg, "head")
                    logger.info("✓ Database stamped with current migration state")
        finally:
            db.close()

        # Run Alembic upgrade to head (this will be a no-op if already at head)
        command.upgrade(alembic_cfg, "head")
        logger.info("✓ Database migrations completed successfully")
    except Exception as e:
        logger.exception("Failed to run startup migrations", exc_info=e)
        # Don't crash the app, just log the error
        logger.warning("Application will continue, but some features may not work correctly")
