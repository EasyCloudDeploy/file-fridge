"""File moving service."""
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from app.config import translate_path_for_symlink
from app.models import MonitoredPath, OperationType

logger = logging.getLogger(__name__)

# Progress tracking thresholds
PROGRESS_THRESHOLD_MB = 10
PROGRESS_UPDATE_BYTES = 1024 * 1024


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
        # Pre-check: verify source exists (important for network mounts with stale entries)
        if not source.exists() and not source.is_symlink():
            # File disappeared - common on network mounts when other apps modify files
            logger.debug(f"Source file no longer exists: {source}")
            return False, f"Source file no longer exists: {source}"

        # Ensure destination directory exists BEFORE checking disk space
        # (disk_usage fails on non-existent paths)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Check available space for operations that copy data
        if operation_type in [OperationType.MOVE, OperationType.COPY] or (operation_type == OperationType.SYMLINK and not source.is_symlink()):
            try:
                file_size = source.stat().st_size
                _, _, free_space = shutil.disk_usage(destination.parent)
                if file_size + (1024 * 1024) > free_space:
                    return False, f"Not enough space for {source.name}. Required: {file_size}, Available: {free_space}"
            except FileNotFoundError:
                # Source file doesn't exist
                if not source.exists():
                    return False, f"Source file disappeared: {source}"
                return False, f"Cannot access source file: {source}"
            except Exception as e:
                logger.warning(f"Could not check disk space: {e}")

        if operation_type == OperationType.MOVE:
            return _move(source, destination, progress_callback)
        if operation_type == OperationType.COPY:
            return _copy(source, destination, progress_callback)
        if operation_type == OperationType.SYMLINK:
            return _move_and_symlink(source, destination, progress_callback)
        return False, f"Unknown operation type: {operation_type}"
    except Exception as e:
        return False, str(e)


def _move(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
    """Move file (atomic if same filesystem, otherwise copy+delete)."""
    try:
        if source.is_symlink():
            return _move_symlink(source, destination, progress_callback)

        # Try atomic rename first (same filesystem)
        try:
            source.rename(destination)
            return True, None
        except OSError:
            # Cross-filesystem move
            _copy_with_progress(source, destination, progress_callback)
            source.unlink()
            return True, None
    except Exception as e:
        return False, f"Move failed: {e!s}"


def _move_symlink(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
    """Handle moving a symlink."""
    try:
        symlink_target = source.readlink()
        if symlink_target.is_absolute():
            resolved_target = Path(symlink_target)
        else:
            resolved_target = (source.parent / symlink_target).resolve()

        # If symlink already points to destination, just remove the symlink
        if resolved_target.resolve() == destination.resolve():
            source.unlink()
            return True, None

        # Move the actual file
        actual_file = source.resolve(strict=True)
        if actual_file.resolve() == destination.resolve():
            source.unlink()
            return True, None

        try:
            actual_file.rename(destination)
        except OSError:
            _copy_with_progress(actual_file, destination, progress_callback)
            actual_file.unlink()

        source.unlink()
        return True, None
    except (OSError, RuntimeError) as e:
        return False, f"Failed to handle symlink: {e!s}"


def _copy(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
    """Copy file preserving metadata."""
    try:
        _copy_with_progress(source, destination, progress_callback)
        return True, None
    except Exception as e:
        return False, f"Copy failed: {e!s}"


def _copy_with_progress(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> None:
    """Copy file with optional progress tracking and timestamp preservation."""
    stat_info = source.stat()
    file_size = stat_info.st_size
    should_report_progress = progress_callback and file_size > (PROGRESS_THRESHOLD_MB * 1024 * 1024)

    if should_report_progress:
        bytes_transferred = 0
        last_report = 0

        with open(source, "rb") as fsrc, open(destination, "wb") as fdst:
            while True:
                chunk = fsrc.read(64 * 1024)
                if not chunk:
                    break
                fdst.write(chunk)
                bytes_transferred += len(chunk)

                if bytes_transferred - last_report >= PROGRESS_UPDATE_BYTES:
                    progress_callback(bytes_transferred)
                    last_report = bytes_transferred

        if bytes_transferred > last_report:
            progress_callback(bytes_transferred)

        shutil.copystat(str(source), str(destination))
    else:
        shutil.copy2(str(source), str(destination))

    # Preserve original timestamps
    os.utime(str(destination), ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns))


def _move_and_symlink(source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None) -> tuple[bool, Optional[str]]:
    """Move file and create symlink at original location."""
    try:
        original_source = source

        if source.is_symlink():
            symlink_target = source.readlink()
            if symlink_target.is_absolute():
                resolved_target = Path(symlink_target)
            else:
                resolved_target = (source.parent / symlink_target).resolve()

            # If symlink already points to destination, nothing to do
            if resolved_target.resolve() == destination.resolve():
                return True, None

            # Move the actual file to new destination
            actual_file = source.resolve(strict=True)
            source.unlink()
            success, error = _move(actual_file, destination, progress_callback)
            if not success:
                return False, error
        else:
            success, error = _move(source, destination, progress_callback)
            if not success:
                return False, error

        # Create symlink at original location
        try:
            symlink_target = translate_path_for_symlink(str(destination))
            original_source.symlink_to(symlink_target)
            return True, None
        except OSError as e:
            # Try to restore file on symlink failure
            try:
                destination.rename(original_source)
            except:
                pass
            return False, f"Symlink creation failed: {e!s}"
    except Exception as e:
        return False, f"Move and symlink failed: {e!s}"


def preserve_directory_structure(source_path: Path, base_source: Path, base_destination: Path) -> Path:
    """Calculate destination path preserving directory structure."""
    try:
        relative_path = source_path.relative_to(base_source)
        return base_destination / relative_path
    except ValueError:
        return base_destination / source_path.name


# Backward compatibility - class wrapper around module functions
class FileMover:
    """Backward-compatible class wrapper for file operations."""

    move_file = staticmethod(move_file)
    preserve_directory_structure = staticmethod(preserve_directory_structure)
    _move = staticmethod(_move)
    _copy = staticmethod(_copy)
    _move_and_symlink = staticmethod(_move_and_symlink)
