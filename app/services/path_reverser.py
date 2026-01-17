"""Service to reverse file operations when criteria are removed or paths are deleted."""

import logging
import shutil
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import FileRecord, OperationType

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
                    success, error = PathReverser._reverse_file_operation(file_record)
                    if success:
                        results["files_reversed"] += 1
                        # Delete the file record
                        db.delete(file_record)
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
    def _reverse_file_operation(file_record: FileRecord) -> tuple[bool, Optional[str]]:
        """
        Reverse a single file operation.

        Args:
            file_record: The FileRecord to reverse

        Returns:
            (success: bool, error_message: Optional[str])
        """
        try:
            cold_path = Path(file_record.cold_storage_path)
            original_path = Path(file_record.original_path)

            # Check if file exists in cold storage
            if not cold_path.exists():
                return False, f"File not found in cold storage: {cold_path}"

            operation_type = file_record.operation_type

            if operation_type == OperationType.MOVE:
                # Move file back from cold storage to original location
                try:
                    # Ensure destination directory exists
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(cold_path), str(original_path))
                    return True, None
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"

            elif operation_type == OperationType.COPY:
                # For copy, the original should still exist, but if it doesn't, move from cold storage
                if not original_path.exists():
                    try:
                        original_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(cold_path), str(original_path))
                        return True, None
                    except Exception as e:
                        return False, f"Failed to move file back: {e!s}"
                else:
                    # Original exists, just remove from cold storage
                    try:
                        cold_path.unlink()
                        return True, None
                    except Exception as e:
                        return False, f"Failed to remove from cold storage: {e!s}"

            elif operation_type == OperationType.SYMLINK:
                # Remove the symlink at original location if it exists
                if original_path.exists() and original_path.is_symlink():
                    original_path.unlink()

                # Move file back from cold storage to original location
                try:
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(cold_path), str(original_path))
                    return True, None
                except Exception as e:
                    return False, f"Failed to move file back: {e!s}"

            else:
                return False, f"Unknown operation type: {operation_type}"

        except Exception as e:
            return False, str(e)
