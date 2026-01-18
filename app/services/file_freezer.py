"""File freezing service - move files from hot storage to cold storage."""

import logging
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    PinnedFile,
    StorageType,
)
from app.services.audit_trail_service import audit_trail_service
from app.services.checksum_verifier import checksum_verifier
from app.services.file_mover import preserve_directory_structure

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
        initiated_by: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Move a file from hot storage to cold storage.

        Args:
            file: The FileInventory entry of the file to freeze
            monitored_path: The MonitoredPath this file belongs to
            storage_location: The target cold storage location
            pin: If True, pin the file to exclude from future scans
            db: Database session (required for database updates)
            initiated_by: User or system component that initiated the operation

        Returns:
            (success: bool, error_message: Optional[str], cold_storage_path: Optional[str])
        """
        if not db:
            return False, "Database session required", None

        # Lock file record for update
        locked_file = (
            db.query(FileInventory).with_for_update().filter(FileInventory.id == file.id).first()
        )

        if not locked_file:
            return False, f"File record not found: {file.id}", None

        try:
            source_path = Path(locked_file.file_path)

            # Verify file exists in hot storage
            if locked_file.storage_type != StorageType.HOT:
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

            # Calculate checksum before move for verification
            checksum_before = checksum_verifier.calculate_checksum(source_path)

            # Mark file as MIGRATING
            old_status = locked_file.status
            locked_file.status = FileStatus.MIGRATING
            db.commit()

            try:
                # Move file using the path's operation type with rollback
                from app.services.file_mover import move_with_rollback

                success, error, checksum_after = move_with_rollback(
                    source_path,
                    destination_path,
                    monitored_path.operation_type,
                    verify_checksum=True,
                )

                if not success:
                    # Rollback status change
                    locked_file.status = old_status
                    db.commit()
                    return False, f"Failed to move file: {error}", None

                # Create FileRecord entry
                file_record = FileRecord(
                    path_id=monitored_path.id,
                    original_path=str(source_path),
                    cold_storage_path=str(destination_path),
                    cold_storage_location_id=storage_location.id,
                    file_size=locked_file.file_size,
                    operation_type=monitored_path.operation_type,
                    criteria_matched="manual_freeze",
                )
                db.add(file_record)

                # Update FileInventory
                locked_file.storage_type = StorageType.COLD
                locked_file.cold_storage_location_id = storage_location.id
                locked_file.status = FileStatus.ACTIVE
                # For SYMLINK operation, the original path stays (symlink points to cold)
                # For MOVE/COPY, update the file_path to the cold storage location
                if monitored_path.operation_type != OperationType.SYMLINK:
                    locked_file.file_path = str(destination_path)

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

                # Log to audit trail
                audit_trail_service.log_freeze_operation(
                    db=db,
                    file=locked_file,
                    source_path=source_path,
                    dest_path=destination_path,
                    storage_location_id=storage_location.id,
                    checksum_before=checksum_before,
                    checksum_after=checksum_after,
                    success=True,
                    initiated_by=initiated_by or "manual",
                )

                logger.info(
                    f"Froze file: {source_path} -> {destination_path} "
                    f"(location: {storage_location.name}, pinned: {pin})"
                )
                return True, None, str(destination_path)

            except Exception as move_error:
                # Rollback status change on failure
                locked_file.status = old_status
                db.commit()
                raise move_error

        except Exception as e:
            logger.exception(f"Error freezing file: {e!s}")
            if db:
                db.rollback()
            return False, str(e), None
