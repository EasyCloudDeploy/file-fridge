"""File moving service."""
import os
import shutil
from pathlib import Path
from typing import Optional
from app.models import OperationType, MonitoredPath


class FileMover:
    """Handles file operations (move, copy, symlink)."""
    
    @staticmethod
    def move_file(
        source: Path,
        destination: Path,
        operation_type: OperationType,
        path_config: Optional[MonitoredPath] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Move/copy/symlink a file.
        
        Returns:
            (success: bool, error_message: Optional[str])
        """
        try:
            # Ensure destination directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)
            
            if operation_type == OperationType.MOVE:
                return FileMover._move(source, destination)
            elif operation_type == OperationType.COPY:
                return FileMover._copy(source, destination)
            elif operation_type == OperationType.SYMLINK:
                return FileMover._move_and_symlink(source, destination)
            else:
                return False, f"Unknown operation type: {operation_type}"
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def _move(source: Path, destination: Path) -> tuple[bool, Optional[str]]:
        """Move file (atomic if same filesystem, otherwise copy+delete)."""
        try:
            # Check if same filesystem
            source_stat = source.stat()
            dest_stat = destination.parent.stat()
            
            # Try atomic rename first (same filesystem)
            try:
                source.rename(destination)
                return True, None
            except OSError:
                # Cross-filesystem move
                shutil.move(str(source), str(destination))
                return True, None
        except Exception as e:
            return False, f"Move failed: {str(e)}"
    
    @staticmethod
    def _copy(source: Path, destination: Path) -> tuple[bool, Optional[str]]:
        """Copy file preserving metadata."""
        try:
            shutil.copy2(str(source), str(destination))
            return True, None
        except Exception as e:
            return False, f"Copy failed: {str(e)}"
    
    @staticmethod
    def _move_and_symlink(source: Path, destination: Path) -> tuple[bool, Optional[str]]:
        """Move file and create symlink at original location."""
        try:
            # Move the file
            success, error = FileMover._move(source, destination)
            if not success:
                return False, error
            
            # Create symlink at original location
            try:
                source.symlink_to(destination)
                return True, None
            except OSError as e:
                # If symlink creation fails, try to move file back
                try:
                    destination.rename(source)
                except:
                    pass
                return False, f"Symlink creation failed: {str(e)}"
        except Exception as e:
            return False, f"Move and symlink failed: {str(e)}"
    
    @staticmethod
    def preserve_directory_structure(
        source_path: Path,
        base_source: Path,
        base_destination: Path
    ) -> Path:
        """
        Calculate destination path preserving directory structure.
        
        Example:
            source_path: /data/logs/app/2024/app.log
            base_source: /data/logs
            base_destination: /cold/logs
            Returns: /cold/logs/app/2024/app.log
        """
        try:
            relative_path = source_path.relative_to(base_source)
            return base_destination / relative_path
        except ValueError:
            # If source_path is not under base_source, just use filename
            return base_destination / source_path.name

