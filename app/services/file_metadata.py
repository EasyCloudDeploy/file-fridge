"""File metadata extraction utilities."""
import hashlib
import mimetypes
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class FileMetadataExtractor:
    """Service for extracting file metadata."""

    @staticmethod
    def compute_sha256(file_path: Path, chunk_size: int = 8192) -> Optional[str]:
        """
        Compute SHA256 hash of a file.

        Args:
            file_path: Path to the file
            chunk_size: Size of chunks to read (default 8KB)

        Returns:
            SHA256 hash as hex string, or None if error
        """
        try:
            if not file_path.exists() or not file_path.is_file():
                return None

            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    sha256_hash.update(chunk)

            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error computing hash for {file_path}: {e}")
            return None

    @staticmethod
    def extract_metadata(file_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Extract file extension, MIME type, and compute hash.

        Args:
            file_path: Path to the file

        Returns:
            Tuple of (file_extension, mime_type, checksum)
        """
        try:
            # Get file extension
            file_extension = file_path.suffix.lower() if file_path.suffix else None

            # Get MIME type
            mime_type, _ = mimetypes.guess_type(str(file_path))

            # Compute checksum (optional - can be slow for large files)
            # Only compute for files smaller than 1GB to avoid performance issues
            checksum = None
            if file_path.exists():
                file_size = file_path.stat().st_size
                # Compute hash for files smaller than 1GB
                if file_size < 1024 * 1024 * 1024:
                    checksum = FileMetadataExtractor.compute_sha256(file_path)

            return (file_extension, mime_type, checksum)

        except Exception as e:
            logger.error(f"Error extracting metadata for {file_path}: {e}")
            return (None, None, None)

    @staticmethod
    def should_compute_hash(file_size: int, max_size_mb: int = 1024) -> bool:
        """
        Determine if hash should be computed based on file size.

        Args:
            file_size: File size in bytes
            max_size_mb: Maximum file size in MB for hash computation

        Returns:
            True if hash should be computed
        """
        max_size_bytes = max_size_mb * 1024 * 1024
        return file_size < max_size_bytes


# Create global instance
file_metadata_extractor = FileMetadataExtractor()
