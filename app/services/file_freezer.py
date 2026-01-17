"""File freezing service - move files from hot storage to cold storage."""

import logging
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    MonitoredPath,
    OperationType,
    PinnedFile,
    StorageType,
)
from app.services.file_mover import move_file, preserve_directory_structure

logger = logging.getLogger(__name__)


class FileFreezer:
    """Handles moving files from hot storage to cold storage."""

    @staticmethod
    def freeze_file(
        file: FileInventory,
        monitored_path: MonitoredPath,
        storage_location: ColdStorageLocation,
        pin: bool = False,
        db: Optional[Session] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Move a file from hot storage to cold storage.

        Args:
            file: The FileInventory entry of the file to freeze
            monitored_path: The MonitoredPath this file belongs to
            storage_location: The target cold storage location
            pin: If True, pin the file to exclude from future scans
            db: Database session (required for database updates)

        Returns:
            (success: bool, error_message: Optional[str], cold_storage_path: Optional[str])
        """
        if not db:
            return False, "Database session required", None

        try:
            source_path = Path(file.file_path)

            # Verify file exists in hot storage
            if file.storage_type != StorageType.HOT:
                return False, f"File is not in hot storage: {source_path}", None

            if not source_path.exists() and not source_path.is_symlink():
                return False, f"File not found: {source_path}", None

            # Calculate destination path preserving directory structure
            base_source = Path(monitored_path.source_path)
            base_destination = Path(storage_location.path)
            destination_path = preserve_directory_structure(
                source_path, base_source, base_destination
            )

            # Ensure destination directory exists
            destination_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if destination already exists
            if destination_path.exists():
                return False, f"Destination already exists: {destination_path}", None

            # Move file using the path's operation type
            success, error = move_file(
                source_path,
                destination_path,
                monitored_path.operation_type,
                path_config=monitored_path,
            )

            if not success:
                return False, f"Failed to move file: {error}", None

            # Create FileRecord entry
            file_record = FileRecord(
                path_id=monitored_path.id,
                original_path=str(source_path),
                cold_storage_path=str(destination_path),
                cold_storage_location_id=storage_location.id,
                file_size=file.file_size,
                operation_type=monitored_path.operation_type,
                criteria_matched="manual_freeze",
            )
            db.add(file_record)

            # Update FileInventory
            file.storage_type = StorageType.COLD
            file.cold_storage_location_id = storage_location.id
            # For SYMLINK operation, the original path stays (symlink points to cold)
            # For MOVE/COPY, update the file_path to the cold storage location
            if monitored_path.operation_type != OperationType.SYMLINK:
                file.file_path = str(destination_path)

            # If pinning, add to pinned files
            if pin:
                # Use the cold storage path for pinning (so it won't be auto-thawed)
                pin_path = str(destination_path)
                existing = db.query(PinnedFile).filter(PinnedFile.file_path == pin_path).first()

                if not existing:
                    pinned = PinnedFile(path_id=monitored_path.id, file_path=pin_path)
                    db.add(pinned)
                    logger.info(f"Pinned file: {pin_path}")

            db.commit()
            logger.info(
                f"Froze file: {source_path} -> {destination_path} "
                f"(location: {storage_location.name}, pinned: {pin})"
            )
            return True, None, str(destination_path)

        except Exception as e:
            logger.exception(f"Error freezing file: {e!s}")
            if db:
                db.rollback()
            return False, str(e), None
