"""File thawing service - move files back from cold storage."""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models import FileRecord, FileStatus, PinnedFile, StorageType
from app.services.audit_trail_service import audit_trail_service
from app.services.checksum_verifier import checksum_verifier

logger = logging.getLogger(__name__)


class FileThawer:
    """Handles moving files back from cold storage to hot storage."""

    @staticmethod
    def thaw_file(
        file_record: FileRecord,
        pin: bool = False,
        db: Optional[Session] = None,
        initiated_by: Optional[str] = None,
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

            # Check if file exists in cold storage
            if not cold_path.exists():
                return False, f"File not found in cold storage: {cold_path}"

            # Check inventory to see if it's encrypted
            from app.models import FileInventory

            file_inventory = (
                db.query(FileInventory).filter(FileInventory.file_path == str(cold_path)).first()
            )

            is_encrypted = file_inventory.is_encrypted if file_inventory else False

            # Calculate checksum before move for verification
            checksum_before = checksum_verifier.calculate_checksum(cold_path)

            # Decrypt if encrypted, otherwise standard move
            if is_encrypted:
                from app.services.encryption_service import file_encryption_service

                try:
                    # For COPY operations where the original file still exists,
                    # skip decryption (don't overwrite) and just remove the cold storage copy
                    if file_record.operation_type.value == "copy" and original_path.exists():
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
            elif file_record.operation_type.value == "symlink":
                # Remove the symlink at original location if it exists
                if original_path.exists() and original_path.is_symlink():
                    original_path.unlink()
                # Move file back from cold storage, preserving timestamps
                try:
                    FileThawer._move_preserving_timestamps(cold_path, original_path)
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"
            elif file_record.operation_type.value == "copy":
                # For copy, file is still in original location, just remove from cold storage
                # Actually, if it was copied, the original should still exist
                # But if we're thawing, we might want to ensure it's in hot storage
                if not original_path.exists():
                    # Original doesn't exist, move from cold storage, preserving timestamps
                    try:
                        FileThawer._move_preserving_timestamps(cold_path, original_path)
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
                    FileThawer._move_preserving_timestamps(cold_path, original_path)
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"

            # Verify checksum after move (skip for encrypted files as checksum changes)
            checksum_after = None
            if original_path.exists():
                checksum_after = checksum_verifier.calculate_checksum(original_path)
                if not is_encrypted and checksum_before and checksum_after != checksum_before:
                    logger.error(
                        f"Checksum mismatch after thaw: {checksum_before[:16]}... != {checksum_after[:16]}..."
                    )
                    return False, "Checksum verification failed after thaw"

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
    def _move_preserving_timestamps(source: Path, destination: Path) -> None:
        """Move file while preserving all timestamps (mtime, atime)."""
        # Get original timestamps before moving
        stat_info = source.stat()

        # Try atomic rename first (same filesystem - preserves all timestamps)
        try:
            source.rename(destination)
        except OSError:
            # Cross-filesystem move - copy with timestamp preservation
            # Copy file with metadata (preserves mtime and atime)
            shutil.copy2(str(source), str(destination))

            # Explicitly set atime and mtime to original values to ensure preservation
            # Note: ctime cannot be set directly as it's managed by the filesystem
            os.utime(str(destination), ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns))

            # Remove original file
            source.unlink()
