"""Checksum verification service - calculates and verifies file checksums."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class ChecksumVerifier:
    """Service for calculating and verifying file checksums."""

    @staticmethod
    def calculate_checksum(file_path: Path, algorithm: str = "sha256") -> Optional[str]:
        """
        Calculate SHA256 checksum of a file.

        Args:
            file_path: Path to the file
            algorithm: Hash algorithm to use (sha256, sha512, md5)

        Returns:
            Hex-encoded checksum as string, or None if calculation fails
        """
        try:
            hash_func = hashlib.new(algorithm)
            chunk_size = 65536  # 64KB chunks for memory efficiency

            with Path(file_path).open("rb") as f:
                while chunk := f.read(chunk_size):
                    hash_func.update(chunk)

            checksum = hash_func.hexdigest()
            logger.debug(f"Calculated {algorithm} checksum for {file_path}: {checksum[:16]}...")
            return checksum

        except OSError as e:
            logger.warning(f"Failed to calculate checksum for {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error calculating checksum for {file_path}: {e}", exc_info=True
            )
            return None

    @staticmethod
    def verify_checksum(file_path: Path, expected_checksum: str, algorithm: str = "sha256") -> bool:
        """
        Verify file checksum matches expected value.

        Args:
            file_path: Path to the file
            expected_checksum: Expected checksum value
            algorithm: Hash algorithm to use

        Returns:
            True if checksums match, False otherwise
        """
        actual_checksum = ChecksumVerifier.calculate_checksum(file_path, algorithm)
        if actual_checksum is None:
            return False

        matches = actual_checksum.lower() == expected_checksum.lower()
        if not matches:
            logger.warning(
                f"Checksum mismatch for {file_path}: expected {expected_checksum[:16]}..., got {actual_checksum[:16]}..."
            )
        else:
            logger.debug(f"Checksum verified for {file_path}: {actual_checksum[:16]}...")

        return matches

    @staticmethod
    def verify_file_integrity(source_path: Path, dest_path: Path) -> bool:
        """
        Verify source and destination files have identical checksums.

        Args:
            source_path: Source file path
            dest_path: Destination file path

        Returns:
            True if checksums match, False otherwise
        """
        source_checksum = ChecksumVerifier.calculate_checksum(source_path)
        if source_checksum is None:
            logger.error(f"Failed to calculate source checksum for {source_path}")
            return False

        dest_checksum = ChecksumVerifier.calculate_checksum(dest_path)
        if dest_checksum is None:
            logger.error(f"Failed to calculate destination checksum for {dest_path}")
            return False

        matches = source_checksum.lower() == dest_checksum.lower()
        if matches:
            logger.debug(f"Integrity verified: {source_path} == {dest_path}")
        else:
            logger.error(
                f"Integrity check failed: {source_path} ({source_checksum[:16]}...) != {dest_path} ({dest_checksum[:16]}...)"
            )

        return matches

    @staticmethod
    def calculate_checksum_for_file_inventory(file_path: Path, file_size: int) -> Optional[str]:
        """
        Calculate checksum for FileInventory, respecting size limit from config.

        For performance, only calculate checksums for files under a certain size
        (configurable via MAX_FILE_SIZE_MB). Larger files can be calculated
        asynchronously later.

        Args:
            file_path: Path to the file
            file_size: File size in bytes

        Returns:
            Checksum string, or None if file is too large or calculation fails
        """
        max_size_bytes = settings.max_file_size_mb * 1024 * 1024

        if file_size > max_size_bytes:
            logger.debug(
                f"Skipping checksum for large file {file_path} ({file_size} bytes > {max_size_bytes} bytes)"
            )
            return None

        return ChecksumVerifier.calculate_checksum(file_path)

    @staticmethod
    def calculate_checksum_batch(
        file_paths: list[Path], max_workers: int = 4
    ) -> dict[Path, Optional[str]]:
        """
        Calculate checksums for multiple files in parallel.

        Args:
            file_paths: List of file paths to process
            max_workers: Maximum number of parallel workers

        Returns:
            Dictionary mapping file paths to checksums (None if failed)
        """
        results = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(ChecksumVerifier.calculate_checksum, path): path
                for path in file_paths
            }

            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    checksum = future.result()
                    results[path] = checksum
                except Exception as e:
                    logger.error(f"Error calculating checksum for {path}: {e}")
                    results[path] = None

        return results


# Singleton instance
checksum_verifier = ChecksumVerifier()
