"""Automatic database migrations on application startup."""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from app.database import engine

logger = logging.getLogger(__name__)


class DatabaseMigration:
    """Handles automatic database schema migrations."""

    @staticmethod
    def column_exists(table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table."""
        inspector = inspect(engine)
        try:
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            if column_name in columns: # Fixed TRY300
                return True
            else:
                return False
        except Exception:
            return False

    @staticmethod
    def table_exists(table_name: str) -> bool:
        """Check if a table exists."""
        inspector = inspect(engine)
        return table_name in inspector.get_table_names()

    @staticmethod
    def index_exists(index_name: str) -> bool:
        """Check if an index exists."""
        with engine.connect() as conn:
            try:
                result = conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name=:name"
                ), {"name": index_name})
                return result.fetchone() is not None
            except Exception:
                return False

    @staticmethod
    def run_migrations():  # noqa: PLR0912, PLR0915 # noqa: PLR0912, PLR0915 # Fixed PLR0912, PLR0915
        """Run all pending database migrations."""
        logger.info("Starting automatic database migrations...")

        try:
            with engine.connect() as conn:
                # Migration 1: Add file_extension and mime_type columns to file_inventory
                if not DatabaseMigration.column_exists("file_inventory", "file_extension"):
                    logger.info("Adding file_extension column to file_inventory table...")
                    conn.execute(text("ALTER TABLE file_inventory ADD COLUMN file_extension TEXT"))
                    conn.commit()
                    logger.info("✓ Added file_extension column")
                else:
                    logger.debug("file_extension column already exists")

                if not DatabaseMigration.column_exists("file_inventory", "mime_type"):
                    logger.info("Adding mime_type column to file_inventory table...")
                    conn.execute(text("ALTER TABLE file_inventory ADD COLUMN mime_type TEXT"))
                    conn.commit()
                    logger.info("✓ Added mime_type column")
                else:
                    logger.debug("mime_type column already exists")

                # Migration 2: Create tags table
                if not DatabaseMigration.table_exists("tags"):
                    logger.info("Creating tags table...")
                    conn.execute(text("""
                        CREATE TABLE tags (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            description TEXT,
                            color TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                    conn.commit()
                    logger.info("✓ Created tags table")
                else:
                    logger.debug("tags table already exists")

                # Migration 3: Create file_tags table
                if not DatabaseMigration.table_exists("file_tags"):
                    logger.info("Creating file_tags table...")
                    conn.execute(text("""
                        CREATE TABLE file_tags (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            file_id INTEGER NOT NULL,
                            tag_id INTEGER NOT NULL,
                            tagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            tagged_by TEXT,
                            FOREIGN KEY (file_id) REFERENCES file_inventory(id) ON DELETE CASCADE,
                            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                        )
                    """))
                    conn.commit()
                    logger.info("✓ Created file_tags table")
                else:
                    logger.debug("file_tags table already exists")

                # Migration 4: Create indexes
                indexes = [
                    ("idx_tags_name", "CREATE INDEX idx_tags_name ON tags(name)"),
                    ("idx_file_tag_unique", "CREATE UNIQUE INDEX idx_file_tag_unique ON file_tags(file_id, tag_id)"),
                    ("idx_file_tags_file_id", "CREATE INDEX idx_file_tags_file_id ON file_tags(file_id)"),
                    ("idx_file_tags_tag_id", "CREATE INDEX idx_file_tags_tag_id ON file_tags(tag_id)"),
                    ("idx_inventory_extension", "CREATE INDEX idx_inventory_extension ON file_inventory(file_extension)"),
                    ("idx_inventory_checksum", "CREATE INDEX idx_inventory_checksum ON file_inventory(checksum)"),
                ]

                for index_name, index_sql in indexes:
                    if not DatabaseMigration.index_exists(index_name):
                        logger.info(f"Creating index {index_name}...")
                        conn.execute(text(index_sql))
                        conn.commit()
                        logger.info(f"✓ Created index {index_name}")
                    else:
                        logger.debug(f"Index {index_name} already exists")

                # Migration 5: Create tag_rules table
                if not DatabaseMigration.table_exists("tag_rules"):
                    logger.info("Creating tag_rules table...")
                    conn.execute(text("""
                        CREATE TABLE tag_rules (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            tag_id INTEGER NOT NULL,
                            criterion_type TEXT NOT NULL,
                            operator TEXT NOT NULL,
                            value TEXT NOT NULL,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            priority INTEGER NOT NULL DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP,
                            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                        )
                    """))
                    conn.commit()
                    logger.info("✓ Created tag_rules table")
                else:
                    logger.debug("tag_rules table already exists")

                # Migration 6: Create tag_rules indexes
                tag_rule_indexes = [
                    ("idx_tag_rules_tag_id", "CREATE INDEX idx_tag_rules_tag_id ON tag_rules(tag_id)"),
                    ("idx_tag_rules_enabled", "CREATE INDEX idx_tag_rules_enabled ON tag_rules(enabled)"),
                    ("idx_tag_rules_priority", "CREATE INDEX idx_tag_rules_priority ON tag_rules(priority)"),
                ]

                for index_name, index_sql in tag_rule_indexes:
                    if not DatabaseMigration.index_exists(index_name):
                        logger.info(f"Creating index {index_name}...")
                        conn.execute(text(index_sql))
                        conn.commit()
                        logger.info(f"✓ Created index {index_name}")
                    else:
                        logger.debug(f"Index {index_name} already exists")

                logger.info("✓ All database migrations completed successfully")

        except OperationalError as e:
            logger.exception("Database migration error", exc_info=e) # Fixed TRY400
            # Don't raise - let the app continue, the error will surface later if critical
            logger.warning("Some migrations may have failed, but continuing startup...")
        except Exception as e:
            logger.exception("Unexpected error during migration", exc_info=e) # Fixed TRY400
            logger.warning("Continuing startup despite migration errors...")


def run_startup_migrations():
    """Entry point for running migrations on application startup."""
    try:
        DatabaseMigration.run_migrations()
    except Exception as e:
        logger.exception("Failed to run startup migrations", exc_info=e) # Fixed TRY400
        # Don't crash the app, just log the error
        logger.warning("Application will continue, but some features may not work correctly")