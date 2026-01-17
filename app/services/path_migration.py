"""Service for handling cold storage path migrations."""

import logging
import shutil
from pathlib import Path
from typing import Dict, Tuple

from sqlalchemy.orm import Session

from app.models import FileInventory, FileRecord

logger = logging.getLogger(__name__)


class PathMigrationService:
    """Handles migration of files when cold storage path changes."""

    @staticmethod
    def check_existing_files(old_path: str, path_id: int, db: Session) -> Dict:
        """
        Check if there are files in the old cold storage location.

        Args:
            old_path: The old cold storage path
            path_id: The monitored path ID
            db: Database session

        Returns:
            Dictionary with file counts and paths
        """
        # Check database records
        file_records = (
            db.query(FileRecord)
            .filter(
                FileRecord.path_id == path_id, FileRecord.cold_storage_path.like(f"{old_path}%")
            )
            .all()
        )

        file_inventory = (
            db.query(FileInventory)
            .filter(
                FileInventory.path_id == path_id,
                FileInventory.file_path.like(f"{old_path}%"),
                FileInventory.storage_type == "cold",
            )
            .all()
        )

        # Check actual filesystem
        old_path_obj = Path(old_path)
        filesystem_files = []
        if old_path_obj.exists():
            filesystem_files = list(old_path_obj.rglob("*"))
            filesystem_files = [
                f for f in filesystem_files if f.is_file() and not f.name.startswith(".")
            ]

        return {
            "file_records_count": len(file_records),
            "inventory_count": len(file_inventory),
            "filesystem_count": len(filesystem_files),
            "has_files": len(file_records) > 0
            or len(file_inventory) > 0
            or len(filesystem_files) > 0,
            "file_records": file_records,
            "file_inventory": file_inventory,
            "filesystem_files": filesystem_files,
        }

    @staticmethod
    def migrate_files(
        old_path: str, new_path: str, path_id: int, db: Session
    ) -> Tuple[bool, str, Dict]:
        """
        Migrate all files from old cold storage path to new path.

        Args:
            old_path: The old cold storage path
            new_path: The new cold storage path
            path_id: The monitored path ID
            db: Database session

        Returns:
            Tuple of (success, error_message, migration_stats)
        """
        logger.info(f"Starting migration from {old_path} to {new_path} for path {path_id}")

        stats = {"files_moved": 0, "files_failed": 0, "records_updated": 0, "errors": []}

        old_path_obj = Path(old_path)
        new_path_obj = Path(new_path)

        # Create new path if it doesn't exist
        try:
            new_path_obj.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created new cold storage directory: {new_path}")
        except Exception as e:
            error_msg = f"Failed to create new cold storage directory: {e}"
            logger.exception(error_msg)
            return False, error_msg, stats

        # Get all files that need to be migrated
        check_result = PathMigrationService.check_existing_files(old_path, path_id, db)

        if not check_result["has_files"]:
            logger.info("No files to migrate")
            return True, None, stats

        # Migrate physical files
        if old_path_obj.exists():
            for old_file in check_result["filesystem_files"]:
                try:
                    # Calculate relative path
                    relative_path = old_file.relative_to(old_path_obj)
                    new_file = new_path_obj / relative_path

                    # Create parent directory
                    new_file.parent.mkdir(parents=True, exist_ok=True)

                    # Move file
                    logger.debug(f"Moving {old_file} -> {new_file}")
                    shutil.move(str(old_file), str(new_file))

                    # Preserve timestamps
                    stat_info = new_file.stat()
                    logger.debug(f"File moved successfully, size: {stat_info.st_size}")

                    stats["files_moved"] += 1

                except Exception as e:
                    error_msg = f"Failed to move {old_file}: {e}"
                    logger.exception(error_msg)
                    stats["errors"].append(error_msg)
                    stats["files_failed"] += 1

        # Update database records
        try:
            # Update FileRecord entries
            for record in check_result["file_records"]:
                old_cold_path = record.cold_storage_path
                # Replace old path with new path
                new_cold_path = old_cold_path.replace(old_path, new_path, 1)
                record.cold_storage_path = new_cold_path
                stats["records_updated"] += 1
                logger.debug(f"Updated FileRecord: {old_cold_path} -> {new_cold_path}")

            # Update FileInventory entries
            for inventory in check_result["file_inventory"]:
                old_inv_path = inventory.file_path
                new_inv_path = old_inv_path.replace(old_path, new_path, 1)
                inventory.file_path = new_inv_path
                stats["records_updated"] += 1
                logger.debug(f"Updated FileInventory: {old_inv_path} -> {new_inv_path}")

            db.commit()
            logger.info(f"Updated {stats['records_updated']} database records")

        except Exception as e:
            db.rollback()
            error_msg = f"Failed to update database records: {e}"
            logger.exception(error_msg)
            stats["errors"].append(error_msg)
            return False, error_msg, stats

        # Clean up old directory if empty
        try:
            if old_path_obj.exists() and not any(old_path_obj.iterdir()):
                old_path_obj.rmdir()
                logger.info(f"Removed empty old directory: {old_path}")
        except Exception as e:
            logger.warning(f"Could not remove old directory {old_path}: {e}")

        success = stats["files_failed"] == 0
        error_msg = (
            None if success else f"Migration completed with {stats['files_failed']} failures"
        )

        logger.info(
            f"Migration complete: {stats['files_moved']} files moved, "
            f"{stats['files_failed']} failed, {stats['records_updated']} records updated"
        )

        return success, error_msg, stats

    @staticmethod
    def abandon_files(old_path: str, path_id: int, db: Session) -> Tuple[bool, str]:
        """
        Mark files in old location as abandoned (no database updates).

        Args:
            old_path: The old cold storage path
            path_id: The monitored path ID
            db: Database session

        Returns:
            Tuple of (success, message)
        """
        logger.warning(
            f"Abandoning files in old cold storage path {old_path} for path {path_id}. "
            f"Files will remain in old location but won't be tracked."
        )

        # Optionally, we could mark FileRecords as "abandoned" or delete them
        # For now, we'll just log the action and leave records as-is
        # The next scan will clean them up as "missing"

        return (
            True,
            f"Files in {old_path} have been left in place. They will be marked as missing on next scan.",
        )
