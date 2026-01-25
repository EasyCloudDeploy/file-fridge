"""Database migration utilities."""

import logging

from alembic.config import Config

from alembic import command
from app.database import engine

logger = logging.getLogger(__name__)


def run_startup_migrations() -> None:
    """Run database migrations using Alembic on application startup."""
    try:
        # Run Alembic upgrade to head
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
        command.upgrade(alembic_cfg, "head")
        logger.info("✓ Database migrations completed successfully")
    except Exception as e:
        logger.exception("Failed to run startup migrations", exc_info=e)
        # Don't crash the app, just log the error
        logger.warning("Application will continue, but some features may not work correctly")
