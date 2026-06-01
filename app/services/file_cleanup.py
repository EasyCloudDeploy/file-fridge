"""File cleanup service - removes records for files that no longer exist."""

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ColdStorageLocation, FileInventory, FileRecord, OperationType
from app.services.cold_storage_backends import get_backend

logger = logging.getLogger(__name__)


class FileCleanup:
    """Handles cleanup of file records for files that no longer exist."""

    # Maximum file size for suspected symlinks (symlinks are typically path strings)
    MAX_SYMLINK_SIZE = 200

    @staticmethod
    def cleanup_missing_files(db: Session, path_id: Optional[int] = None) -> dict:
        """
        Clean up FileRecord and FileInventory entries for files that no longer exist.

        Args:
            db: Database session
            path_id: Optional path ID to limit cleanup to a specific path

        Returns:
            dict with cleanup results
        """
        # Cleanup missing FileInventory entries first
        inventory_results = FileCleanup.cleanup_missing_inventory_files(db, path_id=path_id)

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
                    cold_location = None
                    cold_exists = None

                    if file_record.cold_storage_location_id:
                        cold_location = (
                            db.query(ColdStorageLocation)
                            .filter(ColdStorageLocation.id == file_record.cold_storage_location_id)
                            .first()
                        )
                        if cold_location:
                            try:
                                backend = get_backend(cold_location)
                                if backend.backend_name() != "local":
                                    cold_exists = backend.exists(
                                        file_record.cold_storage_path, cold_location
                                    )
                            except Exception as backend_error:
                                logger.warning(
                                    "Backend existence check failed for FileRecord %s: %s",
                                    file_record.id,
                                    backend_error,
                                )

                    # Determine if the cold_storage_path is a local filesystem path
                    # (no URI scheme). Remote paths (s3://, gdrive://) should never
                    # fall back to a local stat check.
                    cold_path_str = file_record.cold_storage_path or ""
                    is_local_cold_path = "://" not in cold_path_str

                    # Check based on operation type
                    if file_record.operation_type == OperationType.MOVE:
                        # For move, file should be in cold storage
                        if cold_exists is None and is_local_cold_path:
                            cold_exists = check_path_exists(Path(cold_path_str))
                        if cold_exists is False:
                            should_remove = True
                            logger.info(
                                "File not found in cold storage (move): %s",
                                file_record.cold_storage_path,
                            )
                        elif cold_exists is None:
                            logger.warning(
                                "Cannot verify cold storage existence (move), skipping removal: %s",
                                file_record.cold_storage_path,
                            )

                    elif file_record.operation_type == OperationType.COPY:
                        # For copy, check if at least one copy exists
                        original_path = Path(file_record.original_path)
                        if cold_exists is None and is_local_cold_path:
                            cold_exists = check_path_exists(Path(cold_path_str))

                        # If both original and cold storage are confirmed missing, remove record
                        if cold_exists is None:
                            logger.warning(
                                "Cannot verify cold storage existence (copy), skipping removal: %s",
                                file_record.cold_storage_path,
                            )
                        elif not check_path_exists(original_path) and not cold_exists:
                            should_remove = True
                            logger.info(
                                "Both original and cold storage files missing (copy): %s, %s",
                                original_path,
                                file_record.cold_storage_path,
                            )
                        # If at least one exists, keep the record

                    elif file_record.operation_type == OperationType.SYMLINK:
                        # For symlink, check if symlink exists or if cold storage file exists
                        original_path = Path(file_record.original_path)
                        if cold_exists is None:
                            cold_path = Path(file_record.cold_storage_path)
                            cold_exists = check_path_exists(cold_path)

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
                        elif not cold_exists:
                            # Neither symlink nor cold storage file exists
                            should_remove = True
                            logger.info(
                                "Both symlink and cold storage missing: %s, %s",
                                original_path,
                                file_record.cold_storage_path,
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
                f"FileRecord cleanup complete: checked {results['checked']}, removed {results['removed']}"
            )

        except Exception as e:
            error_msg = f"Error during FileRecord cleanup: {e!s}"
            results["errors"].append(error_msg)
            logger.exception(error_msg)
            db.rollback()

        # Combine results
        return {
            "checked": results["checked"] + inventory_results["checked"],
            "removed": results["removed"] + inventory_results["removed"],
            "errors": results["errors"] + inventory_results["errors"],
        }

    @staticmethod
    def cleanup_missing_inventory_files(db: Session, path_id: Optional[int] = None) -> dict:
        """
        Clean up FileInventory entries for files that are marked as 'missing'.

        Args:
            db: Database session
            path_id: Optional path ID to limit cleanup to a specific path

        Returns:
            dict with cleanup results
        """
        results = {"checked": 0, "removed": 0, "errors": []}
        try:
            # Query for missing FileInventory entries
            query = db.query(FileInventory).filter(FileInventory.status == "missing")
            if path_id:
                query = query.filter(FileInventory.path_id == path_id)

            missing_files = query.all()
            results["checked"] = len(missing_files)

            if not missing_files:
                logger.info("No missing FileInventory entries to clean up.")
                return results

            logger.info(f"Found {results['checked']} missing FileInventory entries to remove.")

            for file_entry in missing_files:
                db.delete(file_entry)
                results["removed"] += 1

            db.commit()
            logger.info(
                f"FileInventory cleanup complete: checked {results['checked']}, removed {results['removed']}"
            )

        except Exception as e:
            error_msg = f"Error during FileInventory cleanup: {e!s}"
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

    @staticmethod
    def cleanup_symlink_inventory_entries(db: Session, path_id: Optional[int] = None) -> dict:
        """
        Clean up FileInventory entries that are symlinks.

        Symlinks should not be tracked in the inventory - they're managed separately
        during the scan phase. This cleanup removes any symlink entries that may have
        been added before this fix was implemented.

        Args:
            db: Database session
            path_id: Optional path ID to limit cleanup to a specific path

        Returns:
            dict with cleanup results including:
            - checked: Number of inventory entries checked
            - removed: Number of symlink entries removed
            - errors: List of error messages
        """
        results = {"checked": 0, "removed": 0, "errors": []}

        try:
            # Query all file inventory entries
            query = db.query(FileInventory)
            if path_id:
                query = query.filter(FileInventory.path_id == path_id)

            inventory_entries = query.all()
            results["checked"] = len(inventory_entries)

            for entry in inventory_entries:
                try:
                    file_path = Path(entry.file_path)

                    # Check if the file exists and is a symlink
                    if file_path.exists():
                        try:
                            # Check if it's a symlink
                            if file_path.is_symlink():
                                db.delete(entry)
                                results["removed"] += 1
                                logger.info(
                                    f"Removed symlink from inventory: {entry.id} - {entry.file_path}"
                                )
                                continue
                        except OSError as e:
                            logger.debug(f"Error checking if {file_path} is symlink: {e}")

                    # Also remove entries with suspiciously small file sizes that might be symlinks
                    # Symlinks are typically < MAX_SYMLINK_SIZE bytes (path string length)
                    # AND marked as missing (since they're no longer being scanned)
                    from datetime import datetime, timedelta, timezone

                    now = datetime.now(tz=timezone.utc)
                    last_seen_time = entry.last_seen
                    if last_seen_time.tzinfo is None:
                        last_seen_time = last_seen_time.replace(tzinfo=timezone.utc)
                    is_stale_missing = now - last_seen_time > timedelta(hours=24)

                    if (
                        entry.file_size < FileCleanup.MAX_SYMLINK_SIZE
                        and entry.status.value == "missing"
                        and entry.checksum is not None
                        and is_stale_missing
                    ):
                        # This heuristic catches symlinks that existed before but are now missing
                        # The checksum indicates it was scanned at some point
                        # Small size + missing status suggests it might be a stale symlink entry
                        logger.info(
                            f"Removing suspected symlink entry (small size + missing for >24h): {entry.id} - {entry.file_path}"
                        )
                        db.delete(entry)
                        results["removed"] += 1

                except Exception as e:
                    error_msg = f"Error checking inventory entry {entry.id}: {e!s}"
                    results["errors"].append(error_msg)
                    logger.debug(error_msg)

            db.commit()
            logger.info(
                f"Symlink cleanup complete: checked {results['checked']}, removed {results['removed']}"
            )

        except Exception as e:
            error_msg = f"Error during symlink cleanup: {e!s}"
            results["errors"].append(error_msg)
            logger.exception(error_msg)
            db.rollback()

        return results
