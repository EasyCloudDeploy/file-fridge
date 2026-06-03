"""Service to reverse file operations when criteria are removed or paths are deleted."""

import logging
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import ColdStorageLocation, FileRecord, OperationType
from app.services.cold_storage_backends import get_backend
from app.services.file_thawer import FileThawer

logger = logging.getLogger(__name__)


class PathReverser:
    """Handles reversing file operations for a path."""

    @staticmethod
    def reverse_path_operations(path_id: int, db: Session) -> Dict[str, any]:
        """
        Reverse all file operations for a path (move files back from cold storage).

        Args:
            path_id: The path ID to reverse operations for
            db: Database session

        Returns:
            dict with results:
            - files_reversed: int - number of files successfully moved back
            - errors: List[str] - list of error messages
        """
        results = {"files_reversed": 0, "errors": []}

        try:
            # Get all file records for this path
            file_records = db.query(FileRecord).filter(FileRecord.path_id == path_id).all()

            logger.info(
                f"Reversing operations for path {path_id}: {len(file_records)} files to process"
            )

            for file_record in file_records:
                try:
                    success, error = PathReverser._reverse_file_operation(file_record, db)
                    if success:
                        results["files_reversed"] += 1
                        existing = (
                            db.query(FileRecord).filter(FileRecord.id == file_record.id).first()
                        )
                        if existing is not None:
                            db.delete(existing)
                        logger.info(f"Reversed operation for file: {file_record.original_path}")
                    else:
                        results["errors"].append(
                            f"Failed to reverse {file_record.original_path}: {error}"
                        )
                        logger.error(f"Failed to reverse {file_record.original_path}: {error}")
                except Exception as e:
                    error_msg = f"Error reversing {file_record.original_path}: {e!s}"
                    results["errors"].append(error_msg)
                    logger.exception(error_msg)

            db.commit()
            logger.info(
                f"Path reversal complete: {results['files_reversed']} files reversed, {len(results['errors'])} errors"
            )

        except Exception as e:
            error_msg = f"Error during path reversal: {e!s}"
            results["errors"].append(error_msg)
            logger.exception(error_msg)
            db.rollback()

        return results

    @staticmethod
    def _reverse_file_operation(file_record: FileRecord, db: Session) -> tuple[bool, Optional[str]]:
        """
        Reverse a single file operation.

        Args:
            file_record: The FileRecord to reverse
            db: Database session

        Returns:
            (success: bool, error_message: Optional[str])
        """
        location = None
        if file_record.cold_storage_location_id:
            location = (
                db.query(ColdStorageLocation)
                .filter(ColdStorageLocation.id == file_record.cold_storage_location_id)
                .first()
            )
        if location is not None:
            backend = get_backend(location)
            if backend.backend_name() != "local":
                return FileThawer.thaw_file(
                    file_record=file_record, pin=False, db=db, initiated_by="reverse"
                )

        cold_path = Path(file_record.cold_storage_path)
        original_path = Path(file_record.original_path)
        operation_type = file_record.operation_type

        if not cold_path.exists():
            return False, f"File not found in cold storage: {cold_path}"

        from app.services.file_mover import FileMover

        if operation_type == OperationType.MOVE:
            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                success, error, _ = FileMover.move_with_rollback(
                    source=cold_path,
                    destination=original_path,
                    operation_type=OperationType.MOVE,
                    verify_checksum=True,
                )
                if not success:
                    return False, error or "Failed to move file back during reversal"
                return True, None
            except Exception as e:
                return False, f"Failed to move file back: {e!s}"

        if operation_type == OperationType.COPY:
            if not original_path.exists():
                try:
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                    success, error, _ = FileMover.move_with_rollback(
                        source=cold_path,
                        destination=original_path,
                        operation_type=OperationType.MOVE,
                        verify_checksum=True,
                    )
                    if not success:
                        return False, error or "Failed to move file back during reversal"
                    return True, None
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"
            try:
                cold_path.unlink()
                return True, None
            except Exception as e:
                return False, f"Failed to remove from cold storage: {e!s}"

        if operation_type == OperationType.SYMLINK:
            if original_path.exists() and original_path.is_symlink():
                original_path.unlink()
            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                success, error, _ = FileMover.move_with_rollback(
                    source=cold_path,
                    destination=original_path,
                    operation_type=OperationType.MOVE,
                    verify_checksum=True,
                )
                if not success:
                    return False, error or "Failed to move file back during reversal"
                return True, None
            except Exception as e:
                return False, f"Failed to move file back: {e!s}"

        return False, f"Unknown operation type: {operation_type}"
