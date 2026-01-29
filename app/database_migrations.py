"""Database migration utilities."""

import logging

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.database import SessionLocal, engine

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

            # If we have tables but no alembic version, we need to determine
            # the correct version to stamp to based on the actual schema
            if not has_version and len(tables) > (1 if has_alembic_table else 0):
                logger.info(
                    "Database tables exist but alembic version is not set. "
                    "Determining correct version based on schema..."
                )

                # Check if instance_metadata has the new columns
                # This tells us if we should stamp to head or to the previous version
                if "instance_metadata" in tables:
                    instance_metadata_cols = {
                        col["name"] for col in inspector.get_columns("instance_metadata")
                    }
                    has_new_columns = "instance_url" in instance_metadata_cols

                    if has_new_columns:
                        # Columns exist, safe to stamp to head
                        logger.info("New columns detected, stamping to head...")
                        command.stamp(alembic_cfg, "head")
                    else:
                        # Columns don't exist, stamp to version before the migration
                        # so the migration will run and add them
                        logger.info(
                            "New columns not detected, stamping to add_missing_instance_metadata "
                            "to allow migration to run..."
                        )
                        command.stamp(alembic_cfg, "add_missing_instance_metadata")
                else:
                    # No instance_metadata table, stamp to head (it will be created by init_db)
                    command.stamp(alembic_cfg, "head")

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
