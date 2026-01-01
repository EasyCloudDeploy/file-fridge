"""Database setup and session management."""
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings
import logging

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {}
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


def migrate_add_error_message_column():
    """Add error_message column to monitored_paths table if it doesn't exist."""
    if "sqlite" not in settings.database_url:
        # For non-SQLite databases, use proper migrations
        return

    try:
        inspector = inspect(engine)
        # Check if table exists
        if 'monitored_paths' not in inspector.get_table_names():
            logger.debug("monitored_paths table doesn't exist yet, will be created by create_all")
            return

        # Check if column exists
        columns = [col['name'] for col in inspector.get_columns('monitored_paths')]

        if 'error_message' not in columns:
            logger.info("Adding error_message column to monitored_paths table...")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE monitored_paths ADD COLUMN error_message TEXT"))
            logger.info("Successfully added error_message column")
        else:
            logger.debug("error_message column already exists")
    except Exception as e:
        logger.warning(f"Error during migration: {e}")
        # If table doesn't exist yet, create_all will handle it
        pass


def migrate_add_criteria_columns():
    """Add file_atime and file_ctime columns to file_inventory table if they don't exist."""
    if "sqlite" not in settings.database_url:
        # For non-SQLite databases, use proper migrations
        return

    try:
        inspector = inspect(engine)
        # Check if table exists
        if 'file_inventory' not in inspector.get_table_names():
            logger.debug("file_inventory table doesn't exist yet, will be created by create_all")
            return

        # Check if columns exist
        columns = [col['name'] for col in inspector.get_columns('file_inventory')]

        if 'file_atime' not in columns:
            logger.info("Adding file_atime column to file_inventory table...")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE file_inventory ADD COLUMN file_atime TIMESTAMP"))
            logger.info("Successfully added file_atime column")
        else:
            logger.debug("file_atime column already exists")

        if 'file_ctime' not in columns:
            logger.info("Adding file_ctime column to file_inventory table...")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE file_inventory ADD COLUMN file_ctime TIMESTAMP"))
            logger.info("Successfully added file_ctime column")
        else:
            logger.debug("file_ctime column already exists")
    except Exception as e:
        logger.warning(f"Error during migration: {e}")
        # If table doesn't exist yet, create_all will handle it
        pass


def migrate_add_prevent_indexing_column():
    """Add prevent_indexing column to monitored_paths table if it doesn't exist."""
    if "sqlite" not in settings.database_url:
        # For non-SQLite databases, use proper migrations
        return

    try:
        inspector = inspect(engine)
        # Check if table exists
        if 'monitored_paths' not in inspector.get_table_names():
            logger.debug("monitored_paths table doesn't exist yet, will be created by create_all")
            return

        # Check if column exists
        columns = [col['name'] for col in inspector.get_columns('monitored_paths')]

        if 'prevent_indexing' not in columns:
            logger.info("Adding prevent_indexing column to monitored_paths table...")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE monitored_paths ADD COLUMN prevent_indexing BOOLEAN NOT NULL DEFAULT 1"))
            logger.info("Successfully added prevent_indexing column (default: enabled)")
        else:
            logger.debug("prevent_indexing column already exists")
    except Exception as e:
        logger.warning(f"Error during migration: {e}")
        # If table doesn't exist yet, create_all will handle it
        pass


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
    # Run migrations for existing databases
    migrate_add_error_message_column()
    migrate_add_criteria_columns()
    migrate_add_prevent_indexing_column()

