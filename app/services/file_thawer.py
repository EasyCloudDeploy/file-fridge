"""File thawing service - move files back from cold storage."""
import os
import shutil
import logging
from pathlib import Path
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.models import FileRecord, PinnedFile, MonitoredPath
from app.services.file_mover import FileMover

logger = logging.getLogger(__name__)


class FileThawer:
    """Handles moving files back from cold storage to hot storage."""
    
    @staticmethod
    def thaw_file(
        file_record: FileRecord,
        pin: bool = False,
        db: Optional[Session] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Move a file back from cold storage to hot storage.
        
        Args:
            file_record: The FileRecord of the file to thaw
            pin: If True, pin the file to exclude from future scans
            db: Database session (required if pin=True)
        
        Returns:
            (success: bool, error_message: Optional[str])
        """
        try:
            cold_path = Path(file_record.cold_storage_path)
            original_path = Path(file_record.original_path)
            
            # Check if file exists in cold storage
            if not cold_path.exists():
                return False, f"File not found in cold storage: {cold_path}"
            
            # If original was a symlink, we need to handle it differently
            if file_record.operation_type.value == "symlink":
                # Remove the symlink at original location if it exists
                if original_path.exists() and original_path.is_symlink():
                    original_path.unlink()
                # Move file back from cold storage
                try:
                    shutil.move(str(cold_path), str(original_path))
                except Exception as e:
                    return False, f"Failed to move file back: {str(e)}"
            elif file_record.operation_type.value == "copy":
                # For copy, file is still in original location, just remove from cold storage
                # Actually, if it was copied, the original should still exist
                # But if we're thawing, we might want to ensure it's in hot storage
                if not original_path.exists():
                    # Original doesn't exist, move from cold storage
                    try:
                        shutil.move(str(cold_path), str(original_path))
                    except Exception as e:
                        return False, f"Failed to move file back: {str(e)}"
                else:
                    # Original exists, just remove from cold storage
                    try:
                        cold_path.unlink()
                    except Exception as e:
                        return False, f"Failed to remove from cold storage: {str(e)}"
            else:  # MOVE
                # Move file back from cold storage to original location
                try:
                    # Ensure destination directory exists
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(cold_path), str(original_path))
                except Exception as e:
                    return False, f"Failed to move file back: {str(e)}"
            
            # If pinning, add to pinned files
            if pin and db:
                # Check if already pinned
                existing = db.query(PinnedFile).filter(
                    PinnedFile.file_path == str(original_path)
                ).first()
                
                if not existing:
                    pinned = PinnedFile(
                        path_id=file_record.path_id,
                        file_path=str(original_path)
                    )
                    db.add(pinned)
                    db.commit()
                    logger.info(f"Pinned file: {original_path}")
            
            logger.info(f"Thawed file: {cold_path} -> {original_path} (pinned: {pin})")
            return True, None
            
        except Exception as e:
            logger.error(f"Error thawing file: {str(e)}")
            return False, str(e)

