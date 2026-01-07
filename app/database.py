"""Database setup and session management."""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings
import logging
from pathlib import Path

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
            logger.error(f"Failed to create database directory {db_dir}: {e}")
            raise


# Ensure database directory exists before creating engine
ensure_database_directory()

# SQLite-specific configuration
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
)

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

    NOTE: This function no longer creates database tables directly.
    All database schema changes are now handled exclusively by Alembic migrations.
    See app/database_migrations.py for the migration system.

    This function is kept for backwards compatibility and may perform future
    database initialization tasks that don't involve schema changes.
    """
    # Database directory is already ensured at module import time
    # All schema changes are handled by Alembic migrations
    pass

