"""File moving service."""
import os
import shutil
from pathlib import Path
import logging
from typing import Optional
from app.models import OperationType, MonitoredPath

logger = logging.getLogger(__name__)

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
        """Move file (atomic if same filesystem, otherwise copy+delete) while preserving timestamps."""
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
                            # Cross-filesystem - preserve timestamps
                            FileMover._move_with_timestamps(destination, source)
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
                            # Cross-filesystem move - preserve timestamps
                            FileMover._move_with_timestamps(actual_file, destination)
                            source.unlink()
                            return True, None
                except (OSError, RuntimeError) as e:
                    return False, f"Failed to handle symlink: {str(e)}"

            # Regular file - normal move
            # Try atomic rename first (same filesystem - preserves all timestamps)
            try:
                logger.info(f"  🔧 Attempting atomic rename: {source} -> {destination}")
                source.rename(destination)
                logger.info(f"  ✅ Atomic rename successful (same filesystem, timestamps preserved)")
                return True, None
            except OSError as e:
                # Cross-filesystem move - use copy with timestamp preservation
                logger.info(f"  ⚠️ Atomic rename failed ({e}), using cross-filesystem move with timestamp preservation")
                FileMover._move_with_timestamps(source, destination)
                return True, None
        except Exception as e:
            return False, f"Move failed: {str(e)}"

    @staticmethod
    def _move_with_timestamps(source: Path, destination: Path) -> None:
        """Move file across filesystems while preserving all timestamps."""
        import time as time_module

        # Get original timestamps before copying
        stat_info = source.stat()
        logger.info(f"  📊 _move_with_timestamps: SOURCE timestamps - atime={stat_info.st_atime} ({time_module.ctime(stat_info.st_atime)}), mtime={stat_info.st_mtime} ({time_module.ctime(stat_info.st_mtime)})")

        # Copy file with metadata (preserves mtime and atime)
        logger.info(f"  🔧 Calling shutil.copy2({source} -> {destination})")
        shutil.copy2(str(source), str(destination))

        # Check timestamps after copy2
        post_copy_stat = destination.stat()
        logger.info(f"  📊 AFTER shutil.copy2(): atime={post_copy_stat.st_atime} ({time_module.ctime(post_copy_stat.st_atime)}), mtime={post_copy_stat.st_mtime} ({time_module.ctime(post_copy_stat.st_mtime)})")

        # Explicitly set atime and mtime to original values
        # Note: ctime cannot be set directly as it's managed by the filesystem,
        # but copy2 preserves mtime and atime
        logger.info(f"  🔧 Calling os.utime() with nanoseconds: atime_ns={stat_info.st_atime_ns}, mtime_ns={stat_info.st_mtime_ns}")
        os.utime(str(destination), ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns))

        # Verify timestamps after os.utime
        post_utime_stat = destination.stat()
        logger.info(f"  ✅ AFTER os.utime(): atime={post_utime_stat.st_atime} ({time_module.ctime(post_utime_stat.st_atime)}), mtime={post_utime_stat.st_mtime} ({time_module.ctime(post_utime_stat.st_mtime)})")

        # Check if preservation worked
        atime_diff = abs(post_utime_stat.st_atime - stat_info.st_atime)
        mtime_diff = abs(post_utime_stat.st_mtime - stat_info.st_mtime)
        if atime_diff > 0.001 or mtime_diff > 0.001:  # 1ms tolerance
            logger.error(f"  ❌ TIMESTAMP MISMATCH in _move_with_timestamps! atime diff={atime_diff}s, mtime diff={mtime_diff}s")
        else:
            logger.info(f"  ✅ Timestamps preserved correctly in _move_with_timestamps")

        # Remove original file
        logger.info(f"  🗑️ Unlinking source file: {source}")
        source.unlink()
    
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
            logger.info(f"  🔧 _move_and_symlink: Moving file from {source} to {destination}")
            success, error = FileMover._move(source, destination)
            if not success:
                logger.error(f"Move failed: {error}")
                return False, error

            logger.info(f"  ✅ File moved successfully, now creating symlink")

            # Create symlink at original location
            try:
                logger.info(f"  🔧 Creating symlink: {source} -> {destination}")
                source.symlink_to(destination)
                logger.info(f"  ✅ Symlink created successfully")
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

