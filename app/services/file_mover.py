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
            # If source is a symlink, we need to handle it specially
            if source.is_symlink():
                try:
                    symlink_target = source.readlink()
                    # Resolve to absolute path
                    if symlink_target.is_absolute():
                        resolved_target = Path(symlink_target)
                    else:
                        resolved_target = (source.parent / symlink_target).resolve()
                    
                    # If symlink points to destination, file is already in cold storage
                    # For MOVE operation: move the file back to hot storage first, then to cold storage
                    # This ensures the file is actually moved, not just the symlink removed
                    if resolved_target.resolve() == destination.resolve():
                        # Remove the symlink first
                        source.unlink()
                        # Move the actual file from cold storage back to hot storage
                        try:
                            destination.rename(source)
                        except OSError:
                            # Cross-filesystem - use shutil
                            shutil.move(str(destination), str(source))
                        # Now move it to cold storage (normal move - source is now the actual file)
                        # Recursively call _move, but now source is a regular file, not a symlink
                        return FileMover._move(source, destination)
                    else:
                        # Symlink points elsewhere - move the actual file (not the symlink)
                        actual_file = source.resolve(strict=True)
                        # Check if actual file is already at destination
                        if actual_file.resolve() == destination.resolve():
                            # File is already at destination, just remove symlink
                            source.unlink()
                            return True, None
                        # Move the actual file
                        try:
                            actual_file.rename(destination)
                            # Remove the symlink
                            source.unlink()
                            return True, None
                        except OSError:
                            # Cross-filesystem move
                            shutil.move(str(actual_file), str(destination))
                            source.unlink()
                            return True, None
                except (OSError, RuntimeError) as e:
                    return False, f"Failed to handle symlink: {str(e)}"
            
            # Regular file - normal move
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
            # If source is already a symlink pointing to destination, we need to:
            # 1. Remove the symlink
            # 2. Move the actual file from cold storage back to hot storage
            # 3. Then move it to cold storage again and create new symlink
            if source.is_symlink():
                try:
                    symlink_target = source.readlink()
                    # Resolve to absolute path
                    if symlink_target.is_absolute():
                        resolved_target = Path(symlink_target)
                    else:
                        resolved_target = (source.parent / symlink_target).resolve()
                    
                    # If symlink points to destination, move the actual file back first
                    if resolved_target.resolve() == destination.resolve():
                        # Remove the symlink first
                        source.unlink()
                        # Move the actual file from cold storage back to hot storage
                        try:
                            destination.rename(source)
                        except OSError:
                            # Cross-filesystem - use shutil
                            shutil.move(str(destination), str(source))
                        # Now continue with normal move and symlink operation
                        # (file is now in hot storage, will be moved to cold storage)
                    else:
                        # Symlink points elsewhere - get the actual file
                        actual_file = source.resolve(strict=True)
                        # Remove symlink and use actual file as source
                        source.unlink()
                        source = actual_file
                except (OSError, RuntimeError) as e:
                    return False, f"Failed to handle existing symlink: {str(e)}"
            
            # Regular file - normal move and symlink
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

