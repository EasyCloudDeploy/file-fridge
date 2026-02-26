"""Database setup and session management."""

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

logger = logging.getLogger(__name__)


def ensure_database_directory():
    """Ensure the database directory exists for SQLite."""
    # Get the database file path
    db_path = settings.database_path

    # Handle relative paths (./data/file_fridge.db)
    if db_path.startswith("./"):
        db_path = db_path[2:]

    # Get the directory path
    db_file = Path(db_path)
    db_dir = db_file.parent

    # Create directory if it doesn't exist
    if db_dir and str(db_dir) != ".":
        try:
            db_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured database directory exists: {db_dir}")
        except Exception as e:
            logger.exception("Failed to create database directory", exc_info=e)  # Fixed TRY400
            raise


# Ensure database directory exists before creating engine
ensure_database_directory()

# SQLite-specific configuration
# Use StaticPool for :memory: databases so all connections share a single
# underlying connection and see the same data (critical for tests).
_engine_kwargs: dict = {"connect_args": {"check_same_thread": False}}
if settings.database_path == ":memory:":
    _engine_kwargs["poolclass"] = StaticPool
engine = create_engine(settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency for getting database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Initialize database environment.

    Creates all database tables if they don't exist. This is safe to call
    on existing databases as SQLAlchemy's create_all is idempotent.
    Incremental schema changes are handled by Alembic migrations.
    See app/database_migrations.py for the migration system.
    """
    # Import models here to avoid circular import
    # (models.py imports Base from this file)
    from app import models  # noqa: F401

    # Create all tables that don't exist
    # This is idempotent - existing tables are not affected
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")
