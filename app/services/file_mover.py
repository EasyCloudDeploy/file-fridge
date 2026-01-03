"""File moving service."""
import os
import shutil
from pathlib import Path
import logging
from typing import Optional, Callable
from app.models import OperationType, MonitoredPath
from app.config import translate_path_for_symlink

logger = logging.getLogger(__name__)

# Progress tracking thresholds
PROGRESS_THRESHOLD_MB = 10  # Report progress for files larger than 10MB
PROGRESS_UPDATE_BYTES = 1024 * 1024  # Update every 1MB

class FileMover:
    """Handles file operations (move, copy, symlink)."""
    
    @staticmethod
    def move_file(
        source: Path,
        destination: Path,
        operation_type: OperationType,
        path_config: Optional[MonitoredPath] = None,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Move/copy/symlink a file.

        Args:
            source: Source file path
            destination: Destination file path
            operation_type: Type of operation (MOVE, COPY, SYMLINK)
            path_config: Optional monitored path configuration
            progress_callback: Optional callback(bytes_transferred) for progress updates

        Returns:
            (success: bool, error_message: Optional[str])
        """
        try:
            # Pre-flight check for available space, but only if we are not just moving a symlink
            if operation_type in [OperationType.MOVE, OperationType.COPY] or (operation_type == OperationType.SYMLINK and not source.is_symlink()):
                try:
                    file_size = source.stat().st_size
                    _, _, free_space = shutil.disk_usage(destination.parent)
                    
                    # Add a small buffer (1MB) to be safe
                    if file_size + (1024 * 1024) > free_space:
                        return False, f"Not enough space on destination device for {source.name}. Required: {file_size}, Available: {free_space}"
                except FileNotFoundError:
                    # Source file not found, will be caught later but good to handle
                    return False, f"Source file not found: {source}"
                except Exception as e:
                    logger.warning(f"Could not check disk space for {destination.parent}: {e}")

            # Ensure destination directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)

            if operation_type == OperationType.MOVE:
                return FileMover._move(source, destination, progress_callback)
            elif operation_type == OperationType.COPY:
                return FileMover._copy(source, destination, progress_callback)
            elif operation_type == OperationType.SYMLINK:
                return FileMover._move_and_symlink(source, destination, progress_callback)
            else:
                return False, f"Unknown operation type: {operation_type}"
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def _move(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
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

                    # If symlink points to destination, file is already in cold storage at the correct location
                    # For MOVE operation: just remove the symlink, file is already where it needs to be
                    if resolved_target.resolve() == destination.resolve():
                        logger.info(f"  File already at destination in cold storage, removing symlink")
                        source.unlink()
                        return True, None
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
                            FileMover._move_with_timestamps(actual_file, destination, progress_callback)
                            source.unlink()
                            return True, None
                except (OSError, RuntimeError) as e:
                    return False, f"Failed to handle symlink: {str(e)}"

            # Regular file - normal move
            # Try atomic rename first (same filesystem - preserves all timestamps)
            try:
                logger.info(f"  Attempting atomic rename: {source} -> {destination}")
                source.rename(destination)
                logger.info(f"  Atomic rename successful (same filesystem, timestamps preserved)")
                return True, None
            except OSError as e:
                # Cross-filesystem move - use copy with timestamp preservation
                logger.info(f"  Atomic rename failed ({e}), using cross-filesystem move with timestamp preservation")
                FileMover._move_with_timestamps(source, destination, progress_callback)
                return True, None
        except Exception as e:
            return False, f"Move failed: {str(e)}"

    @staticmethod
    def _move_with_timestamps(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> None:
        """Move file across filesystems while preserving all timestamps."""
        import time as time_module

        # Get original timestamps before copying
        stat_info = source.stat()
        file_size = stat_info.st_size
        logger.info(f"  _move_with_timestamps: SOURCE timestamps - atime={stat_info.st_atime} ({time_module.ctime(stat_info.st_atime)}), mtime={stat_info.st_mtime} ({time_module.ctime(stat_info.st_mtime)})")

        # Check if file is large enough to report progress
        should_report_progress = progress_callback and file_size > (PROGRESS_THRESHOLD_MB * 1024 * 1024)

        if should_report_progress:
            # Use manual copy with progress tracking for large files
            logger.info(f"  Copying large file ({file_size / 1024 / 1024:.1f} MB) with progress tracking")
            bytes_transferred = 0
            last_report = 0

            with open(source, 'rb') as fsrc:
                with open(destination, 'wb') as fdst:
                    while True:
                        chunk = fsrc.read(64 * 1024)  # 64KB chunks
                        if not chunk:
                            break
                        fdst.write(chunk)
                        bytes_transferred += len(chunk)

                        # Report progress every PROGRESS_UPDATE_BYTES
                        if bytes_transferred - last_report >= PROGRESS_UPDATE_BYTES:
                            progress_callback(bytes_transferred)
                            last_report = bytes_transferred

            # Final progress update
            if bytes_transferred > last_report:
                progress_callback(bytes_transferred)

            # Copy metadata separately
            shutil.copystat(str(source), str(destination))
        else:
            # Use fast copy for small files
            logger.info(f"  Calling shutil.copy2({source} -> {destination})")
            shutil.copy2(str(source), str(destination))

        # Check timestamps after copy
        post_copy_stat = destination.stat()
        logger.info(f"  AFTER copy: atime={post_copy_stat.st_atime} ({time_module.ctime(post_copy_stat.st_atime)}), mtime={post_copy_stat.st_mtime} ({time_module.ctime(post_copy_stat.st_mtime)})")

        # Explicitly set atime and mtime to original values
        logger.info(f"  Calling os.utime() with nanoseconds: atime_ns={stat_info.st_atime_ns}, mtime_ns={stat_info.st_mtime_ns}")
        os.utime(str(destination), ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns))

        # Verify timestamps after os.utime
        post_utime_stat = destination.stat()
        logger.info(f"  AFTER os.utime(): atime={post_utime_stat.st_atime} ({time_module.ctime(post_utime_stat.st_atime)}), mtime={post_utime_stat.st_mtime} ({time_module.ctime(post_utime_stat.st_mtime)})")

        # Check if preservation worked
        atime_diff = abs(post_utime_stat.st_atime - stat_info.st_atime)
        mtime_diff = abs(post_utime_stat.st_mtime - stat_info.st_mtime)
        if atime_diff > 0.001 or mtime_diff > 0.001:  # 1ms tolerance
            logger.error(f"  TIMESTAMP MISMATCH in _move_with_timestamps! atime diff={atime_diff}s, mtime diff={mtime_diff}s")
        else:
            logger.info(f"  Timestamps preserved correctly in _move_with_timestamps")

        # Remove original file
        logger.info(f"  Unlinking source file: {source}")
        source.unlink()
    
    @staticmethod
    def _copy(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
        """Copy file preserving metadata."""
        try:
            shutil.copy2(str(source), str(destination))
            return True, None
        except Exception as e:
            return False, f"Copy failed: {str(e)}"
    
    @staticmethod
    def _move_and_symlink(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
        """Move file and create symlink at original location."""
        try:
            # Save the original source location for symlink creation
            original_source = source

            # If source is already a symlink, handle it specially
            if source.is_symlink():
                try:
                    symlink_target = source.readlink()
                    # Resolve to absolute path
                    if symlink_target.is_absolute():
                        resolved_target = Path(symlink_target)
                    else:
                        resolved_target = (source.parent / symlink_target).resolve()

                    # If symlink points to destination, file is already in place with correct symlink
                    if resolved_target.resolve() == destination.resolve():
                        logger.info(f"  File already at destination with symlink in place, nothing to do")
                        return True, None
                    else:
                        # Symlink points elsewhere - need to move the actual file to new destination
                        actual_file = source.resolve(strict=True)
                        logger.info(f"  Symlink points to {actual_file}, moving to {destination}")
                        # Remove symlink first
                        source.unlink()
                        # Move the actual file from old location to new destination
                        success, error = FileMover._move(actual_file, destination, progress_callback)
                        if not success:
                            logger.error(f"Move failed: {error}")
                            return False, error
                        # Continue to create symlink at original source location
                except (OSError, RuntimeError) as e:
                    return False, f"Failed to handle existing symlink: {str(e)}"
            else:
                # Regular file - normal move
                logger.info(f"  _move_and_symlink: Moving file from {source} to {destination}")
                success, error = FileMover._move(source, destination, progress_callback)
                if not success:
                    logger.error(f"Move failed: {error}")
                    return False, error

            logger.info(f"  File moved successfully, now creating symlink")

            # Create symlink at original location
            try:
                # Translate destination path for symlink (container -> host path)
                symlink_target = translate_path_for_symlink(str(destination))
                logger.info(f"  Creating symlink: {original_source} -> {symlink_target}")
                if symlink_target != str(destination):
                    logger.info(f"  Path translated for symlink: {destination} -> {symlink_target}")
                original_source.symlink_to(symlink_target)
                logger.info(f"  Symlink created successfully")
                return True, None
            except OSError as e:
                # If symlink creation fails, try to move file back
                try:
                    destination.rename(original_source)
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

