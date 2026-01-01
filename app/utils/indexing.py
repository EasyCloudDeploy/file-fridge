"""Utilities for managing macOS Spotlight indexing."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class IndexingManager:
    """Manages .noindex files to prevent macOS Spotlight from indexing directories."""

    NOINDEX_FILENAME = ".noindex"

    @staticmethod
    def create_noindex_file(directory: str) -> bool:
        """
        Create a .noindex file in the specified directory to prevent Spotlight indexing.

        Args:
            directory: Path to the directory where .noindex file should be created

        Returns:
            True if file was created or already exists, False on error
        """
        try:
            dir_path = Path(directory)

            # Create directory if it doesn't exist
            if not dir_path.exists():
                logger.info(f"Creating directory for .noindex: {directory}")
                dir_path.mkdir(parents=True, exist_ok=True)

            noindex_path = dir_path / IndexingManager.NOINDEX_FILENAME

            # Create .noindex file if it doesn't exist
            if not noindex_path.exists():
                logger.info(f"Creating .noindex file to prevent Spotlight indexing: {noindex_path}")
                noindex_path.touch()
                return True
            else:
                logger.debug(f".noindex file already exists: {noindex_path}")
                return True

        except Exception as e:
            logger.error(f"Failed to create .noindex file in {directory}: {e}")
            return False

    @staticmethod
    def remove_noindex_file(directory: str) -> bool:
        """
        Remove the .noindex file from the specified directory to allow Spotlight indexing.

        Args:
            directory: Path to the directory where .noindex file should be removed

        Returns:
            True if file was removed or doesn't exist, False on error
        """
        try:
            dir_path = Path(directory)

            if not dir_path.exists():
                logger.debug(f"Directory doesn't exist, nothing to remove: {directory}")
                return True

            noindex_path = dir_path / IndexingManager.NOINDEX_FILENAME

            # Remove .noindex file if it exists
            if noindex_path.exists():
                logger.info(f"Removing .noindex file to allow Spotlight indexing: {noindex_path}")
                noindex_path.unlink()
                return True
            else:
                logger.debug(f".noindex file doesn't exist: {noindex_path}")
                return True

        except Exception as e:
            logger.error(f"Failed to remove .noindex file from {directory}: {e}")
            return False

    @staticmethod
    def manage_noindex_files(source_path: str, cold_storage_path: str, prevent_indexing: bool) -> bool:
        """
        Manage .noindex files for both hot and cold storage directories.

        Args:
            source_path: Path to the hot storage directory
            cold_storage_path: Path to the cold storage directory
            prevent_indexing: If True, create .noindex files; if False, remove them

        Returns:
            True if operation succeeded for both directories, False otherwise
        """
        logger.info(f"Managing .noindex files (prevent_indexing={prevent_indexing})")
        logger.info(f"   Hot storage: {source_path}")
        logger.info(f"   Cold storage: {cold_storage_path}")

        if prevent_indexing:
            # Create .noindex files in both directories
            hot_success = IndexingManager.create_noindex_file(source_path)
            cold_success = IndexingManager.create_noindex_file(cold_storage_path)
        else:
            # Remove .noindex files from both directories
            hot_success = IndexingManager.remove_noindex_file(source_path)
            cold_success = IndexingManager.remove_noindex_file(cold_storage_path)

        success = hot_success and cold_success
        if success:
            logger.info(f"Successfully managed .noindex files for both directories")
        else:
            logger.warning(f"Failed to manage .noindex files for one or more directories")

        return success
