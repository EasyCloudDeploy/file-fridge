"""File cleanup service - removes records for files that no longer exist."""

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import FileRecord, OperationType

logger = logging.getLogger(__name__)


class FileCleanup:
    """Handles cleanup of file records for files that no longer exist."""

    @staticmethod
    def cleanup_missing_files(db: Session, path_id: Optional[int] = None) -> dict:
        """
        Clean up FileRecord entries for files that no longer exist.

        Args:
            db: Database session
            path_id: Optional path ID to limit cleanup to a specific path

        Returns:
            dict with cleanup results
        """
        results = {"checked": 0, "removed": 0, "errors": []}

        # Directory existence cache to avoid redundant filesystem calls
        dir_exists_cache = {}

        def check_path_exists(p: Path) -> bool:
            # If the path itself exists, return True
            if p.exists():
                return True

            # If the path doesn't exist, check if its parent exists
            # (Caching parent existence helps when many files in a missing directory are checked)
            parent = p.parent
            parent_str = str(parent)
            if parent_str in dir_exists_cache and not dir_exists_cache[parent_str]:
                return False  # Parent is known to be missing

            parent_exists = parent.exists()
            dir_exists_cache[parent_str] = parent_exists
            return False

        try:
            # Query all file records
            query = db.query(FileRecord)
            if path_id:
                query = query.filter(FileRecord.path_id == path_id)

            file_records = query.all()
            results["checked"] = len(file_records)

            for file_record in file_records:
                try:
                    should_remove = False

                    # Check based on operation type
                    if file_record.operation_type == OperationType.MOVE:
                        # For move, file should be in cold storage
                        cold_path = Path(file_record.cold_storage_path)
                        if not check_path_exists(cold_path):
                            should_remove = True
                            logger.info(f"File not found in cold storage (move): {cold_path}")

                    elif file_record.operation_type == OperationType.COPY:
                        # For copy, check if at least one copy exists
                        original_path = Path(file_record.original_path)
                        cold_path = Path(file_record.cold_storage_path)

                        # If both original and cold storage don't exist, remove record
                        if not check_path_exists(original_path) and not check_path_exists(
                            cold_path
                        ):
                            should_remove = True
                            logger.info(
                                f"Both original and cold storage files missing (copy): {original_path}, {cold_path}"
                            )
                        # If at least one exists, keep the record

                    elif file_record.operation_type == OperationType.SYMLINK:
                        # For symlink, check if symlink exists or if cold storage file exists
                        original_path = Path(file_record.original_path)
                        cold_path = Path(file_record.cold_storage_path)

                        # If symlink exists, check if it points to existing file
                        if original_path.exists() and original_path.is_symlink():
                            # Symlink exists, check if target exists
                            try:
                                target = original_path.resolve()
                                if not check_path_exists(target):
                                    should_remove = True
                                    logger.info(
                                        f"Symlink target missing: {original_path} -> {target}"
                                    )
                            except Exception:
                                should_remove = True
                                logger.info(f"Symlink broken: {original_path}")
                        elif not check_path_exists(cold_path):
                            # Neither symlink nor cold storage file exists
                            should_remove = True
                            logger.info(
                                f"Both symlink and cold storage missing: {original_path}, {cold_path}"
                            )

                    if should_remove:
                        db.delete(file_record)
                        results["removed"] += 1
                        logger.info(f"Removed FileRecord {file_record.id} for missing file")

                except Exception as e:
                    error_msg = f"Error checking file record {file_record.id}: {e!s}"
                    results["errors"].append(error_msg)
                    logger.exception(error_msg)

            db.commit()
            logger.info(
                f"Cleanup complete: checked {results['checked']}, removed {results['removed']}"
            )

        except Exception as e:
            error_msg = f"Error during cleanup: {e!s}"
            results["errors"].append(error_msg)
            logger.exception(error_msg)
            db.rollback()

        return results

    @staticmethod
    def cleanup_duplicates(db: Session, path_id: Optional[int] = None) -> dict:
        """
        Clean up duplicate FileRecord entries.
        Keeps the most recent record for each unique file.

        Args:
            db: Database session
            path_id: Optional path ID to limit cleanup to a specific path

        Returns:
            dict with cleanup results
        """
        results = {"checked": 0, "removed": 0, "errors": []}

        try:
            # Query all file records
            query = db.query(FileRecord)
            if path_id:
                query = query.filter(FileRecord.path_id == path_id)

            file_records = query.order_by(FileRecord.moved_at.desc()).all()
            results["checked"] = len(file_records)

            # Group by original_path and cold_storage_path
            seen = {}  # (original_path, cold_storage_path) -> first_seen_record

            for file_record in file_records:
                key = (file_record.original_path, file_record.cold_storage_path)

                if key in seen:
                    # Duplicate found - remove the older one (or this one if it's older)
                    existing = seen[key]
                    if file_record.moved_at > existing.moved_at:
                        # Current record is newer, remove the existing one
                        db.delete(existing)
                        seen[key] = file_record
                        results["removed"] += 1
                        logger.info(f"Removed duplicate FileRecord {existing.id} (older) for {key}")
                    else:
                        # Existing record is newer, remove current one
                        db.delete(file_record)
                        results["removed"] += 1
                        logger.info(
                            f"Removed duplicate FileRecord {file_record.id} (older) for {key}"
                        )
                else:
                    seen[key] = file_record

            db.commit()
            logger.info(
                f"Duplicate cleanup complete: checked {results['checked']}, removed {results['removed']}"
            )

        except Exception as e:
            error_msg = f"Error during duplicate cleanup: {e!s}"
            results["errors"].append(error_msg)
            logger.exception(error_msg)
            db.rollback()

        return results
