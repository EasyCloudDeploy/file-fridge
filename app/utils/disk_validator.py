import shutil
from pathlib import Path


class DiskSpaceValidator:
    """Utility class for validating disk space before transfers."""

    @staticmethod
    def _validate(file_size: int, destination_path: Path) -> None:
        if not destination_path.exists():
            msg = f"Destination directory does not exist: {destination_path}"
            raise ValueError(msg)

        _total, _used, free = shutil.disk_usage(destination_path)

        buffer = max(file_size * 0.1, 1024 * 1024)
        required_space = file_size + buffer

        if free < required_space:
            free_mb = free / 1024 / 1024
            required_mb = required_space / 1024 / 1024
            msg = (
                f"Insufficient disk space: {free_mb:.2f} MB free, "
                f"{required_mb:.2f} MB required "
                f"(including 10% buffer)"
            )
            raise ValueError(msg)

    @staticmethod
    def validate_disk_space(file_path: Path, destination_path: Path) -> None:
        """
        Validate that there's sufficient disk space for a transfer.

        Args:
            file_path: Path to source file
            destination_path: Path to destination directory

        Raises:
            ValueError: If insufficient disk space or file doesn't exist
        """
        if not file_path.exists():
            msg = f"Source file does not exist: {file_path}"
            raise ValueError(msg)

        file_size = file_path.stat().st_size
        DiskSpaceValidator._validate(file_size, destination_path)

    @staticmethod
    def validate_disk_space_direct(file_size: int, destination_path: Path) -> None:
        """
        Validate disk space when only file size is known.

        Args:
            file_size: Size of file in bytes
            destination_path: Path to destination directory

        Raises:
            ValueError: If insufficient disk space or destination doesn't exist
        """
        DiskSpaceValidator._validate(file_size, destination_path)


disk_space_validator = DiskSpaceValidator()
