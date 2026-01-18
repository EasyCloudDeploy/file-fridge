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
            return column_name in columns
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
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index' AND name=:name"),
                    {"name": index_name},
                )
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
                    conn.execute(
                        text(
                            """
                        CREATE TABLE tags (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            description TEXT,
                            color TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """
                        )
                    )
                    conn.commit()
                    logger.info("✓ Created tags table")
                else:
                    logger.debug("tags table already exists")

                # Migration 3: Create file_tags table
                if not DatabaseMigration.table_exists("file_tags"):
                    logger.info("Creating file_tags table...")
                    conn.execute(
                        text(
                            """
                        CREATE TABLE file_tags (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            file_id INTEGER NOT NULL,
                            tag_id INTEGER NOT NULL,
                            tagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            tagged_by TEXT,
                            FOREIGN KEY (file_id) REFERENCES file_inventory(id) ON DELETE CASCADE,
                            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                        )
                    """
                        )
                    )
                    conn.commit()
                    logger.info("✓ Created file_tags table")
                else:
                    logger.debug("file_tags table already exists")

                # Migration 4: Create indexes
                indexes = [
                    ("idx_tags_name", "CREATE INDEX idx_tags_name ON tags(name)"),
                    (
                        "idx_file_tag_unique",
                        "CREATE UNIQUE INDEX idx_file_tag_unique ON file_tags(file_id, tag_id)",
                    ),
                    (
                        "idx_file_tags_file_id",
                        "CREATE INDEX idx_file_tags_file_id ON file_tags(file_id)",
                    ),
                    (
                        "idx_file_tags_tag_id",
                        "CREATE INDEX idx_file_tags_tag_id ON file_tags(tag_id)",
                    ),
                    (
                        "idx_inventory_extension",
                        "CREATE INDEX idx_inventory_extension ON file_inventory(file_extension)",
                    ),
                    (
                        "idx_inventory_checksum",
                        "CREATE INDEX idx_inventory_checksum ON file_inventory(checksum)",
                    ),
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
                    conn.execute(
                        text(
                            """
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
                    """
                        )
                    )
                    conn.commit()
                    logger.info("✓ Created tag_rules table")
                else:
                    logger.debug("tag_rules table already exists")

                # Migration 6: Create tag_rules indexes
                tag_rule_indexes = [
                    (
                        "idx_tag_rules_tag_id",
                        "CREATE INDEX idx_tag_rules_tag_id ON tag_rules(tag_id)",
                    ),
                    (
                        "idx_tag_rules_enabled",
                        "CREATE INDEX idx_tag_rules_enabled ON tag_rules(enabled)",
                    ),
                    (
                        "idx_tag_rules_priority",
                        "CREATE INDEX idx_tag_rules_priority ON tag_rules(priority)",
                    ),
                ]

                for index_name, index_sql in tag_rule_indexes:
                    if not DatabaseMigration.index_exists(index_name):
                        logger.info(f"Creating index {index_name}...")
                        conn.execute(text(index_sql))
                        conn.commit()
                        logger.info(f"✓ Created index {index_name}")
                    else:
                        logger.debug(f"Index {index_name} already exists")

                # Migration 7: Add cold_storage_location_id to file_records
                if not DatabaseMigration.column_exists("file_records", "cold_storage_location_id"):
                    logger.info("Adding cold_storage_location_id column to file_records table...")
                    conn.execute(
                        text(
                            "ALTER TABLE file_records ADD COLUMN cold_storage_location_id INTEGER REFERENCES cold_storage_locations(id)"
                        )
                    )
                    conn.commit()
                    logger.info("✓ Added cold_storage_location_id column to file_records")
                else:
                    logger.debug("cold_storage_location_id column already exists in file_records")

                # Migration 8: Add cold_storage_location_id to file_inventory
                if not DatabaseMigration.column_exists(
                    "file_inventory", "cold_storage_location_id"
                ):
                    logger.info("Adding cold_storage_location_id column to file_inventory table...")
                    conn.execute(
                        text(
                            "ALTER TABLE file_inventory ADD COLUMN cold_storage_location_id INTEGER REFERENCES cold_storage_locations(id)"
                        )
                    )
                    conn.commit()
                    logger.info("✓ Added cold_storage_location_id column to file_inventory")
                else:
                    logger.debug("cold_storage_location_id column already exists in file_inventory")

                # Migration 9: Create indexes for cold_storage_location_id
                cold_storage_indexes = [
                    (
                        "idx_file_records_cold_storage_location_id",
                        "CREATE INDEX idx_file_records_cold_storage_location_id ON file_records(cold_storage_location_id)",
                    ),
                    (
                        "idx_file_inventory_cold_storage_location_id",
                        "CREATE INDEX idx_file_inventory_cold_storage_location_id ON file_inventory(cold_storage_location_id)",
                    ),
                ]

                for index_name, index_sql in cold_storage_indexes:
                    if not DatabaseMigration.index_exists(index_name):
                        logger.info(f"Creating index {index_name}...")
                        conn.execute(text(index_sql))
                        conn.commit()
                        logger.info(f"✓ Created index {index_name}")
                    else:
                        logger.debug(f"Index {index_name} already exists")

                # Migration 10: Add scan tracking columns to monitored_paths
                if not DatabaseMigration.column_exists("monitored_paths", "last_scan_at"):
                    logger.info("Adding last_scan_at column to monitored_paths table...")
                    conn.execute(
                        text("ALTER TABLE monitored_paths ADD COLUMN last_scan_at TIMESTAMP")
                    )
                    conn.commit()
                    logger.info("✓ Added last_scan_at column")
                else:
                    logger.debug("last_scan_at column already exists")

                if not DatabaseMigration.column_exists("monitored_paths", "last_scan_status"):
                    logger.info("Adding last_scan_status column to monitored_paths table...")
                    conn.execute(
                        text("ALTER TABLE monitored_paths ADD COLUMN last_scan_status TEXT")
                    )
                    conn.commit()
                    logger.info("✓ Added last_scan_status column")
                else:
                    logger.debug("last_scan_status column already exists")

                if not DatabaseMigration.column_exists("monitored_paths", "last_scan_error_log"):
                    logger.info("Adding last_scan_error_log column to monitored_paths table...")
                    conn.execute(
                        text("ALTER TABLE monitored_paths ADD COLUMN last_scan_error_log TEXT")
                    )
                    conn.commit()
                    logger.info("✓ Added last_scan_error_log column")
                else:
                    logger.debug("last_scan_error_log column already exists")

                if not DatabaseMigration.column_exists("monitored_paths", "created_at"):
                    logger.info("Adding created_at column to monitored_paths table...")
                    conn.execute(
                        text(
                            "ALTER TABLE monitored_paths ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                        )
                    )
                    conn.commit()
                    logger.info("✓ Added created_at column")
                else:
                    logger.debug("created_at column already exists")

                if not DatabaseMigration.column_exists("monitored_paths", "updated_at"):
                    logger.info("Adding updated_at column to monitored_paths table...")
                    conn.execute(
                        text("ALTER TABLE monitored_paths ADD COLUMN updated_at TIMESTAMP")
                    )
                    conn.commit()
                    logger.info("✓ Added updated_at column")
                else:
                    logger.debug("updated_at column already exists")

                # Migration 11: Create remote_connections table
                if not DatabaseMigration.table_exists("remote_connections"):
                    logger.info("Creating remote_connections table...")
                    conn.execute(
                        text(
                            """
                        CREATE TABLE remote_connections (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL,
                            url TEXT NOT NULL,
                            shared_secret TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP
                        )
                    """
                        )
                    )
                    conn.execute(
                        text("CREATE INDEX idx_remote_connections_name ON remote_connections(name)")
                    )
                    conn.commit()
                    logger.info("✓ Created remote_connections table")
                else:
                    logger.debug("remote_connections table already exists")

                # Migration 12: Create remote_transfer_jobs table
                if not DatabaseMigration.table_exists("remote_transfer_jobs"):
                    logger.info("Creating remote_transfer_jobs table...")
                    conn.execute(
                        text(
                            """
                        CREATE TABLE remote_transfer_jobs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            file_inventory_id INTEGER NOT NULL,
                            remote_connection_id INTEGER NOT NULL,
                            remote_monitored_path_id INTEGER NOT NULL,
                            status TEXT NOT NULL,
                            progress INTEGER DEFAULT 0,
                            current_size INTEGER DEFAULT 0,
                            total_size INTEGER NOT NULL,
                            start_time TIMESTAMP,
                            end_time TIMESTAMP,
                            error_message TEXT,
                            retry_count INTEGER DEFAULT 0,
                            source_path TEXT NOT NULL,
                            relative_path TEXT NOT NULL,
                            storage_type TEXT NOT NULL,
                            checksum TEXT,
                            current_speed INTEGER DEFAULT 0,
                            eta INTEGER,
                            FOREIGN KEY (file_inventory_id) REFERENCES file_inventory(id),
                            FOREIGN KEY (remote_connection_id) REFERENCES remote_connections(id) ON DELETE CASCADE
                        )
                    """
                        )
                    )
                    conn.execute(
                        text(
                            "CREATE INDEX idx_remote_transfer_status ON remote_transfer_jobs(status)"
                        )
                    )
                    conn.commit()
                    logger.info("✓ Created remote_transfer_jobs table")
                else:
                    logger.debug("remote_transfer_jobs table already exists")

                logger.info("✓ All database migrations completed successfully")

        except OperationalError as e:
            logger.exception("Database migration error", exc_info=e)  # Fixed TRY400
            # Don't raise - let the app continue, the error will surface later if critical
            logger.warning("Some migrations may have failed, but continuing startup...")
        except Exception as e:
            logger.exception("Unexpected error during migration", exc_info=e)  # Fixed TRY400
            logger.warning("Continuing startup despite migration errors...")


def run_startup_migrations():
    """Entry point for running migrations on application startup."""
    try:
        DatabaseMigration.run_migrations()
    except Exception as e:
        logger.exception("Failed to run startup migrations", exc_info=e)  # Fixed TRY400
        # Don't crash the app, just log the error
        logger.warning("Application will continue, but some features may not work correctly")
