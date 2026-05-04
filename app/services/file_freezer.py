"""File freezing service - move files from hot storage to cold storage."""

import logging
import tempfile
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
from app.services.cold_storage_backends import get_backend
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
            operation_mode = storage_location.operation_mode or monitored_path.operation_type
            # If the storage location defaults to MOVE but the monitored path explicitly
            # requests COPY or SYMLINK, honour the monitored path setting.
            if operation_mode == OperationType.MOVE and monitored_path.operation_type in (
                OperationType.COPY,
                OperationType.SYMLINK,
            ):
                operation_mode = monitored_path.operation_type
            try:
                relative_path = source_path.relative_to(base_source)
            except ValueError:
                relative_path = Path(source_path.name)
            backend = get_backend(storage_location)
            capabilities = backend.capabilities()
            if operation_mode == OperationType.SYMLINK and not capabilities.supports_symlink:
                return (
                    False,
                    f"Operation '{operation_mode.value}' is not supported by backend '{backend.backend_name()}'",
                    None,
                )

            if backend.backend_name() == "local":
                destination_path = preserve_directory_structure(
                    source_path,
                    base_source,
                    Path(storage_location.path),
                )
            else:
                destination_path = relative_path
            logger.info(
                "Preparing manual freeze: file_id=%s source=%s destination=%s operation=%s storage_location=%s",
                locked_file.id,
                source_path,
                destination_path,
                operation_mode,
                storage_location.path,
            )

            # Check encryption
            encrypt_file = storage_location.is_encrypted
            if encrypt_file:
                destination_path = destination_path.with_suffix(destination_path.suffix + ".ffenc")

            if backend.backend_name() == "local":
                # Ensure destination directory exists
                destination_parent_existed = destination_path.parent.exists()
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                logger.debug(
                    "Manual freeze destination parent ready: path=%s existed_before=%s",
                    destination_path.parent,
                    destination_parent_existed,
                )

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
                # Handle encryption or regular move
                if encrypt_file:
                    from app.services.encryption_service import file_encryption_service

                    if backend.backend_name() == "local":
                        try:
                            file_encryption_service.encrypt_file(db, source_path, destination_path)
                            # Delete original if move (not copy)
                            if operation_mode == OperationType.MOVE:
                                source_path.unlink()

                            checksum_after = checksum_verifier.calculate_checksum(destination_path)
                            storage_reference = str(destination_path)
                        except Exception as e:
                            # Rollback status change
                            locked_file.status = old_status
                            db.commit()
                            return False, f"Failed to encrypt/move file: {e}", None
                    else:
                        encrypted_temp_path = None
                        storage_reference = None
                        try:
                            encrypted_relative_path = relative_path.with_suffix(
                                relative_path.suffix + ".ffenc"
                            )
                            with tempfile.NamedTemporaryFile(
                                prefix="file-fridge-", suffix=".ffenc", delete=False
                            ) as encrypted_tmp:
                                encrypted_temp_path = Path(encrypted_tmp.name)

                            file_encryption_service.encrypt_file(
                                db, source_path, encrypted_temp_path
                            )
                            success, error, storage_reference, _checksum_after_remote = (
                                backend.freeze_file(
                                    source_path=encrypted_temp_path,
                                    relative_path=encrypted_relative_path,
                                    location=storage_location,
                                    # Copy encrypted temp into backend, then apply requested behavior to original.
                                    operation_mode=OperationType.COPY,
                                )
                            )
                            if not success:
                                locked_file.status = old_status
                                db.commit()
                                return False, f"Failed to move file: {error}", None

                            if operation_mode == OperationType.MOVE:
                                source_path.unlink()

                            checksum_after = (
                                checksum_verifier.calculate_checksum(source_path)
                                if source_path.exists()
                                else None
                            )
                        except Exception as e:
                            locked_file.status = old_status
                            db.commit()
                            return False, f"Failed to encrypt/move file: {e}", None
                        finally:
                            if encrypted_temp_path and encrypted_temp_path.exists():
                                encrypted_temp_path.unlink()
                else:
                    success, error, storage_reference, checksum_after = backend.freeze_file(
                        source_path=source_path,
                        relative_path=relative_path,
                        location=storage_location,
                        operation_mode=operation_mode,
                    )

                    if not success:
                        # Rollback status change
                        locked_file.status = old_status
                        db.commit()
                        logger.error(
                            "Freeze move failed for %s (op=%s): %s",
                            source_path,
                            operation_mode,
                            error,
                        )
                        return False, f"Failed to move file: {error}", None
                    if storage_reference:
                        destination_path = (
                            Path(storage_reference)
                            if backend.backend_name() == "local"
                            else destination_path
                        )
                    else:
                        storage_reference = str(destination_path)

                # Create FileRecord entry
                cold_storage_path = (
                    str(destination_path)
                    if backend.backend_name() == "local"
                    else (storage_reference or str(destination_path))
                )
                file_record = FileRecord(
                    path_id=monitored_path.id,
                    original_path=str(source_path),
                    cold_storage_path=cold_storage_path,
                    cold_storage_location_id=storage_location.id,
                    file_size=locked_file.file_size,
                    operation_type=operation_mode,
                    criteria_matched="manual_freeze",
                )
                db.add(file_record)

                # Update FileInventory
                locked_file.storage_type = StorageType.COLD
                locked_file.cold_storage_location_id = storage_location.id
                locked_file.status = FileStatus.ACTIVE
                locked_file.is_encrypted = encrypt_file

                # For COPY, the real file stays in hot storage, so we keep the original file_path.
                # For MOVE and SYMLINK, the actual data has moved to cold storage, so the
                # inventory must track the cold path (for SYMLINK, the hot path is just a pointer).
                if operation_mode != OperationType.COPY:
                    locked_file.file_path = cold_storage_path

                # If pinning, add to pinned files
                if pin:
                    # For COPY mode the hot file stays in place, so pin the hot path to
                    # prevent the scanner from auto-moving it. For MOVE/SYMLINK the data
                    # is in cold storage, so pin the cold path.
                    pin_path = (
                        locked_file.file_path
                        if operation_mode == OperationType.COPY
                        else cold_storage_path
                    )
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
                return True, None, cold_storage_path

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
