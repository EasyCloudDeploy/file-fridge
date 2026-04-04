"""File moving service."""

import builtins
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from app.config import translate_path_for_symlink
from app.models import MonitoredPath, OperationType
from app.services.checksum_verifier import checksum_verifier  # Moved to module level

logger = logging.getLogger(__name__)

# Progress tracking thresholds
PROGRESS_THRESHOLD_MB = 10
PROGRESS_UPDATE_BYTES = 1024 * 1024


def move_file(
    source: Path,
    destination: Path,
    operation_type: OperationType,
    path_config: Optional[MonitoredPath] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
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
        if operation_type in [OperationType.MOVE, OperationType.COPY] or (
            operation_type == OperationType.SYMLINK and not source.is_symlink()
        ):
            try:
                file_size = source.stat().st_size
                _, _, free_space = shutil.disk_usage(destination.parent)
                if file_size + (1024 * 1024) > free_space:
                    return (
                        False,
                        f"Not enough space for {source.name}. Required: {file_size}, Available: {free_space}",
                    )
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


def _move(
    source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None
) -> tuple[bool, Optional[str]]:
    """Move file (atomic if same filesystem, otherwise copy+delete)."""
    try:
        logger.debug("Attempting move operation: %s -> %s", source, destination)
        if source.is_symlink():
            return _move_symlink(source, destination, progress_callback)

        # Try atomic rename first (same filesystem)
        try:
            source.rename(destination)
            logger.debug("Move completed via atomic rename: %s -> %s", source, destination)
            return True, None
        except OSError as exc:
            logger.debug(
                "Atomic rename failed for %s -> %s, falling back to copy+delete: %s",
                source,
                destination,
                exc,
            )
            # Cross-filesystem move
            _copy_with_progress(source, destination, progress_callback)
            source.unlink()
            logger.debug("Move completed via copy+delete: %s -> %s", source, destination)
            return True, None
    except Exception as e:
        return False, f"Move failed: {e!s}"


def _move_symlink(
    source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None
) -> tuple[bool, Optional[str]]:
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


def _copy(
    source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None
) -> tuple[bool, Optional[str]]:
    """Copy file preserving metadata."""
    try:
        logger.debug("Attempting copy operation: %s -> %s", source, destination)
        _copy_with_progress(source, destination, progress_callback)
        logger.debug("Copy completed successfully: %s -> %s", source, destination)
        return True, None
    except Exception as e:
        return False, f"Copy failed: {e!s}"


def _copy_with_progress(
    source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None
) -> None:
    """Copy file with optional progress tracking and timestamp preservation."""
    stat_info = source.stat()
    file_size = stat_info.st_size
    should_report_progress = progress_callback is not None

    if should_report_progress:
        bytes_transferred = 0
        last_report = 0
        progress_callback(0)

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


def move_with_rollback(
    source: Path,
    destination: Path,
    operation_type,
    verify_checksum: bool = True,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Move/copy file with rollback on failure.

    This ensures atomic-like behavior: if verification fails, the destination
    is deleted to avoid leaving files in an inconsistent state.

    Args:
        source: Source file path
        destination: Destination file path
        operation_type: Type of operation (MOVE, COPY, SYMLINK)
        verify_checksum: Whether to verify checksum after move
        progress_callback: Optional progress callback

    Returns:
        (success, error_message, checksum) tuple
    """
    logger.info(
        "Starting file transfer: source=%s destination=%s operation=%s verify_checksum=%s",
        source,
        destination,
        operation_type,
        verify_checksum,
    )
    destination_parent_existed = destination.parent.exists()
    # Ensure the target directory exists for direct callers such as the
    # workflow freezer path, which bypasses move_file().
    destination.parent.mkdir(parents=True, exist_ok=True)
    logger.debug(
        "Destination parent ready: path=%s existed_before=%s exists_now=%s",
        destination.parent,
        destination_parent_existed,
        destination.parent.exists(),
    )

    # Calculate source checksum for verification
    source_checksum = None
    if verify_checksum:
        source_checksum = checksum_verifier.calculate_checksum(source)
        logger.debug(
            "Calculated source checksum for transfer: source=%s checksum_prefix=%s",
            source,
            source_checksum[:16] if source_checksum else None,
        )

    # Perform the move operation
    if operation_type == OperationType.MOVE:
        success, error = _move(source, destination, progress_callback)
    elif operation_type == OperationType.COPY:
        success, error = _copy(source, destination, progress_callback)
    elif operation_type == OperationType.SYMLINK:
        success, error = _move_and_symlink(source, destination, progress_callback)
    else:
        return False, f"Unknown operation type: {operation_type}", None

    if not success:
        logger.error("File move failed: %s -> %s (%s)", source, destination, error)
        return False, error, None

    # Verify checksum if requested and source checksum was calculated
    if verify_checksum and source_checksum:
        dest_checksum = checksum_verifier.calculate_checksum(destination)
        logger.debug(
            "Calculated destination checksum for transfer: destination=%s checksum_prefix=%s",
            destination,
            dest_checksum[:16] if dest_checksum else None,
        )

        if dest_checksum != source_checksum:
            logger.error(
                f"Checksum mismatch after move: {source_checksum[:16] if source_checksum else 'None'}... != {dest_checksum[:16] if dest_checksum else 'None'}..."
            )
            # Rollback: delete destination to avoid inconsistent state
            try:
                if destination.exists():
                    destination.unlink()
                logger.info(f"Rolled back move by deleting destination: {destination}")
            except Exception as e:
                logger.error(f"Failed to rollback: {e}")

            return False, "Checksum verification failed", source_checksum

    logger.info(
        "Completed file transfer: source=%s destination=%s operation=%s",
        source,
        destination,
        operation_type,
    )
    return True, None, source_checksum


def _move_and_symlink(
    source: Path, destination: Path, progress_callback: Optional[Callable[[int], None]] = None
) -> tuple[bool, Optional[str]]:
    """Move file and create symlink at original location."""
    try:
        logger.debug("Attempting move+symlink operation: %s -> %s", source, destination)
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
            logger.debug(
                "Move+symlink completed successfully: source=%s destination=%s symlink_target=%s",
                original_source,
                destination,
                symlink_target,
            )
            return True, None
        except OSError as e:
            # Try to restore file on symlink failure
            with contextlib.suppress(builtins.BaseException):
                destination.rename(original_source)
            return False, f"Symlink creation failed: {e!s}"
    except Exception as e:
        return False, f"Move and symlink failed: {e!s}"


def preserve_directory_structure(
    source_path: Path, base_source: Path, base_destination: Path
) -> Path:
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
    move_with_rollback = staticmethod(move_with_rollback)
