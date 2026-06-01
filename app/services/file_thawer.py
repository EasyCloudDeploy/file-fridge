"""File thawing service - move files back from cold storage."""

import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileRecord,
    FileStatus,
    OperationType,
    PinnedFile,
    StorageType,
)
from app.services.audit_trail_service import audit_trail_service
from app.services.checksum_verifier import checksum_verifier
from app.services.cold_storage_backends import get_backend

logger = logging.getLogger(__name__)


class FileThawer:
    """Handles moving files back from cold storage to hot storage."""

    @staticmethod
    def _temp_destination_for(destination: Path) -> Path:
        """Create a deterministic temporary destination path in the target directory."""
        return destination.with_name(f"{destination.name}.tmp")

    @staticmethod
    def _cleanup_temp_destination(temp_destination: Path, final_destination: Path) -> None:
        """Best-effort cleanup for staged thaw files."""
        if temp_destination == final_destination:
            return
        if temp_destination.exists():
            temp_destination.unlink()

    @staticmethod
    def _finalize_staged_move(
        source: Path, prepared_destination: Path, final_destination: Path, stat_info: os.stat_result
    ) -> None:
        """Finalize a staged copy after checksum verification."""
        if prepared_destination != final_destination:
            prepared_destination.replace(final_destination)

        try:
            shutil.copystat(str(source), str(final_destination))
            os.utime(str(final_destination), ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns))
            source.unlink()
        except Exception as e:
            logger.error(
                "Failed to copy metadata or remove source in staged thaw finalization: %s", e
            )
            raise

    @staticmethod
    def thaw_file(
        file_record: FileRecord,
        pin: bool = False,
        db: Optional[Session] = None,
        initiated_by: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Move a file back from cold storage to hot storage while preserving timestamps.

        Args:
            file_record: The FileRecord of the file to thaw
            pin: If True, pin the file to exclude from future scans
            db: Database session (required if pin=True)
            initiated_by: User or system component that initiated the operation

        Returns:
            (success: bool, error_message: Optional[str])
        """
        if not db:
            return False, "Database session required"

        try:
            cold_path = Path(file_record.cold_storage_path)
            original_path = Path(file_record.original_path)
            prepared_path: Optional[Path] = None
            prepared_stat: Optional[os.stat_result] = None
            storage_location = None
            operation_mode = file_record.operation_type
            if file_record.cold_storage_location_id:
                storage_location = (
                    db.query(ColdStorageLocation)
                    .filter(ColdStorageLocation.id == file_record.cold_storage_location_id)
                    .first()
                )

            # Check inventory to see if it's encrypted
            from app.models import FileInventory

            file_inventory = (
                db.query(FileInventory)
                .filter(
                    (FileInventory.file_path == file_record.cold_storage_path)
                    | (FileInventory.file_path == file_record.original_path)
                )
                .first()
            )

            is_encrypted = file_inventory.is_encrypted if file_inventory else False

            backend = get_backend(storage_location) if storage_location else None
            if backend and backend.backend_name() != "local":
                if not backend.exists(file_record.cold_storage_path, storage_location):
                    return False, f"File not found in cold storage: {file_record.cold_storage_path}"

                checksum_before = file_inventory.checksum if file_inventory else None
                checksum_after = None

                if is_encrypted:
                    from app.services.encryption_service import file_encryption_service

                    if operation_mode == OperationType.COPY and original_path.exists():
                        success, error = backend.thaw_file(
                            storage_reference=file_record.cold_storage_path,
                            destination_path=original_path,
                            location=storage_location,
                            operation_mode=operation_mode,
                        )
                        if not success:
                            return False, error
                        checksum_after = checksum_verifier.calculate_checksum(original_path)
                    else:
                        original_path.parent.mkdir(parents=True, exist_ok=True)
                        encrypted_temp = original_path.with_name(
                            f"{original_path.name}.ffenc.download"
                        )
                        decrypted_temp = original_path.with_suffix(original_path.suffix + ".tmp")
                        if encrypted_temp.exists():
                            encrypted_temp.unlink()
                        if decrypted_temp.exists():
                            decrypted_temp.unlink()

                        try:
                            success, error = backend.thaw_file(
                                storage_reference=file_record.cold_storage_path,
                                destination_path=encrypted_temp,
                                location=storage_location,
                                operation_mode=operation_mode,
                            )
                            if not success:
                                return False, error

                            file_encryption_service.decrypt_file(db, encrypted_temp, decrypted_temp)
                            decrypted_temp.replace(original_path)
                            checksum_after = (
                                checksum_verifier.calculate_checksum(original_path)
                                if original_path.exists()
                                else None
                            )
                        finally:
                            if encrypted_temp.exists():
                                encrypted_temp.unlink()
                            if decrypted_temp.exists():
                                decrypted_temp.unlink()
                else:
                    success, error = backend.thaw_file(
                        storage_reference=file_record.cold_storage_path,
                        destination_path=original_path,
                        location=storage_location,
                        operation_mode=operation_mode,
                    )
                    if not success:
                        return False, error

                    if original_path.exists():
                        checksum_after = checksum_verifier.calculate_checksum(original_path)

                db.delete(file_record)

                if pin:
                    existing = (
                        db.query(PinnedFile)
                        .filter(PinnedFile.file_path == str(original_path))
                        .first()
                    )
                    if not existing:
                        db.add(
                            PinnedFile(path_id=file_record.path_id, file_path=str(original_path))
                        )

                db.commit()

                if file_inventory:
                    file_inventory.storage_type = StorageType.HOT
                    file_inventory.status = FileStatus.ACTIVE
                    file_inventory.is_encrypted = False
                    file_inventory.file_path = str(original_path)

                    audit_trail_service.log_thaw_operation(
                        db=db,
                        file=file_inventory,
                        source_path=file_record.cold_storage_path,
                        dest_path=original_path,
                        checksum_before=checksum_before,
                        checksum_after=checksum_after,
                        success=True,
                        initiated_by=initiated_by or "manual",
                    )
                    db.commit()

                return True, None

            # Local backend path
            if not cold_path.exists():
                if storage_location and storage_location.backend_type.value == "local":
                    drive_hint_parts = []
                    if storage_location.local_drive_label:
                        drive_hint_parts.append(f"label={storage_location.local_drive_label}")
                    if storage_location.local_drive_identifier:
                        drive_hint_parts.append(f"id={storage_location.local_drive_identifier}")
                    if storage_location.local_drive_mount_path:
                        drive_hint_parts.append(f"mount={storage_location.local_drive_mount_path}")
                    drive_hint = (
                        f" (expected drive: {', '.join(drive_hint_parts)})"
                        if drive_hint_parts
                        else ""
                    )
                    return False, f"File not found in cold storage: {cold_path}{drive_hint}"
                return False, f"File not found in cold storage: {cold_path}"

            # Calculate checksum before move for verification
            checksum_before = checksum_verifier.calculate_checksum(cold_path)

            # Decrypt if encrypted, otherwise standard move
            if is_encrypted:
                from app.services.encryption_service import file_encryption_service

                try:
                    # For COPY operations where the original file still exists,
                    # skip decryption (don't overwrite) and just remove the cold storage copy
                    if operation_mode.value == "copy" and original_path.exists():
                        cold_path.unlink()
                    else:
                        # Ensure destination directory exists
                        original_path.parent.mkdir(parents=True, exist_ok=True)

                        # If original is a symlink, remove it first
                        if original_path.exists() and original_path.is_symlink():
                            original_path.unlink()

                        # Decrypt to temporary file first for atomic replacement
                        # This avoids following symlinks at original_path and ensures atomicity
                        target_path = original_path.with_suffix(original_path.suffix + ".tmp")

                        try:
                            # Decrypt to temp file
                            file_encryption_service.decrypt_file(db, cold_path, target_path)

                            # Atomically move it to final destination (replaces existing file/symlink)
                            target_path.replace(original_path)

                        except Exception:
                            # Clean up temp file if decryption failed
                            if target_path.exists():
                                target_path.unlink()
                            raise

                        # Remove encrypted file from cold storage
                        cold_path.unlink()

                except Exception as e:
                    return False, f"Failed to decrypt/thaw file: {e}"

            # If original was a symlink, we need to handle it differently (and not encrypted)
            elif operation_mode.value == "symlink":
                # Remove the symlink at original location if it exists
                if original_path.exists() and original_path.is_symlink():
                    original_path.unlink()
                # Move file back from cold storage, preserving timestamps
                try:
                    prepared_path, prepared_stat = FileThawer._move_preserving_timestamps(
                        cold_path, original_path, progress_callback
                    )
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"
            elif operation_mode.value == "copy":
                # For copy, file is still in original location, just remove from cold storage
                # Actually, if it was copied, the original should still exist
                # But if we're thawing, we might want to ensure it's in hot storage
                if not original_path.exists():
                    # Original doesn't exist, move from cold storage, preserving timestamps
                    try:
                        original_path.parent.mkdir(parents=True, exist_ok=True)
                        prepared_path, prepared_stat = FileThawer._move_preserving_timestamps(
                            cold_path, original_path, progress_callback
                        )
                    except Exception as e:
                        return False, f"Failed to move file back: {e!s}"
                else:
                    # Original exists, just remove from cold storage
                    try:
                        cold_path.unlink()
                    except Exception as e:
                        return False, f"Failed to remove from cold storage: {e!s}"
            else:  # MOVE
                # Move file back from cold storage to original location, preserving timestamps
                try:
                    # Ensure destination directory exists
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                    prepared_path, prepared_stat = FileThawer._move_preserving_timestamps(
                        cold_path, original_path, progress_callback
                    )
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"

            # Verify checksum after move (skip for encrypted files as checksum changes)
            checksum_after = None
            verification_path = prepared_path or original_path
            if verification_path.exists():
                checksum_after = checksum_verifier.calculate_checksum(verification_path)
                if not is_encrypted and checksum_before and checksum_after != checksum_before:
                    logger.error(
                        f"Checksum mismatch after thaw: {checksum_before[:16]}... != {checksum_after[:16]}..."
                    )
                    if prepared_path is not None:
                        FileThawer._cleanup_temp_destination(prepared_path, original_path)
                    return False, "Checksum verification failed after thaw"

            if (
                prepared_path is not None
                and prepared_stat is not None
                and prepared_path != original_path
            ):
                try:
                    FileThawer._finalize_staged_move(
                        cold_path, prepared_path, original_path, prepared_stat
                    )
                except Exception as e:
                    FileThawer._cleanup_temp_destination(prepared_path, original_path)
                    return False, f"Failed to finalize thawed file: {e!s}"

            # Delete FileRecord entry
            db.delete(file_record)

            # If pinning, add to pinned files
            if pin:
                # Check if already pinned
                existing = (
                    db.query(PinnedFile).filter(PinnedFile.file_path == str(original_path)).first()
                )

                if not existing:
                    pinned = PinnedFile(path_id=file_record.path_id, file_path=str(original_path))
                    db.add(pinned)
                    logger.info(f"Pinned file: {original_path}")

            db.commit()

            if file_inventory:
                # Update inventory status
                file_inventory.storage_type = StorageType.HOT
                file_inventory.status = FileStatus.ACTIVE
                file_inventory.is_encrypted = False
                file_inventory.file_path = str(original_path)  # Ensure path is updated to hot path

                # Log to audit trail
                audit_trail_service.log_thaw_operation(
                    db=db,
                    file=file_inventory,
                    source_path=cold_path,
                    dest_path=original_path,
                    checksum_before=checksum_before,
                    checksum_after=checksum_after,
                    success=True,
                    initiated_by=initiated_by or "manual",
                )

                db.commit()

            logger.info(f"Thawed file: {cold_path} -> {original_path} (pinned: {pin})")
            return True, None

        except Exception as e:
            logger.exception(f"Error thawing file: {e!s}")
            if db:
                db.rollback()
            return False, str(e)

    @staticmethod
    def _move_preserving_timestamps(
        source: Path,
        destination: Path,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Tuple[Path, os.stat_result]:
        """Move file while preserving all timestamps (mtime, atime)."""
        # Get original timestamps before moving
        stat_info = source.stat()

        # Try atomic rename first (same filesystem - preserves all timestamps)
        try:
            source.rename(destination)
            return destination, stat_info
        except OSError:
            # Cross-filesystem move - copy with timestamp preservation
            bytes_transferred = 0
            last_report = 0
            temp_destination = FileThawer._temp_destination_for(destination)

            if temp_destination.exists():
                temp_destination.unlink()

            try:
                with open(source, "rb") as fsrc, open(temp_destination, "wb") as fdst:
                    while True:
                        chunk = fsrc.read(64 * 1024)
                        if not chunk:
                            break
                        fdst.write(chunk)
                        bytes_transferred += len(chunk)

                        if progress_callback and bytes_transferred - last_report >= 1024 * 1024:
                            progress_callback(bytes_transferred)
                            last_report = bytes_transferred

                if progress_callback and bytes_transferred > last_report:
                    progress_callback(bytes_transferred)
            except Exception:
                FileThawer._cleanup_temp_destination(temp_destination, destination)
                raise

            return temp_destination, stat_info
