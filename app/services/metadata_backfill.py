"""Service for backfilling metadata for existing files in inventory."""

import logging
from pathlib import Path
from typing import Dict

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import FileInventory
from app.services.file_metadata import FileMetadataExtractor

logger = logging.getLogger(__name__)


class MetadataBackfillService:
    """Service for backfilling file metadata (extension, MIME type, checksum)."""

    def __init__(self, db: Session):
        self.db = db

    def backfill_all(self, batch_size: int = 100, compute_checksum: bool = True) -> Dict[str, int]:
        """
        Backfill metadata for all files missing extension, MIME type, or checksum.

        Args:
            batch_size: Number of files to process per batch (default: 100)
            compute_checksum: Whether to compute checksums for files (default: True)

        Returns:
            Dictionary with statistics:
            - files_processed: Total files updated
            - files_skipped: Files that don't exist on disk
            - files_failed: Files that encountered errors
            - total_files: Total files that needed processing
        """
        logger.info("Starting metadata backfill for existing files...")

        # Find all files missing metadata
        files_needing_update = (
            self.db.query(FileInventory)
            .filter(
                or_(
                    FileInventory.file_extension.is_(None),
                    FileInventory.mime_type.is_(None),
                    FileInventory.checksum.is_(None) if compute_checksum else False,
                )
            )
            .all()
        )

        total_files = len(files_needing_update)
        logger.info(f"Found {total_files} files needing metadata updates")

        if total_files == 0:
            return {"files_processed": 0, "files_skipped": 0, "files_failed": 0, "total_files": 0}

        files_processed = 0
        files_skipped = 0
        files_failed = 0
        batch_count = 0

        # Process in batches for better performance
        for i in range(0, total_files, batch_size):
            batch = files_needing_update[i : i + batch_size]
            batch_count += 1

            for file_entry in batch:
                try:
                    result = self._update_file_metadata(
                        file_entry, compute_checksum=compute_checksum
                    )

                    if result == "updated":
                        files_processed += 1
                    elif result == "skipped":
                        files_skipped += 1
                    else:
                        files_failed += 1

                except Exception:
                    logger.exception(f"Error processing file {file_entry.file_path}")
                    files_failed += 1

            # Commit after each batch
            try:
                self.db.commit()
                logger.info(
                    f"Batch {batch_count} complete: "
                    f"Processed {files_processed}/{total_files} files "
                    f"({files_skipped} skipped, {files_failed} failed)"
                )
            except Exception:
                logger.exception(f"Error committing batch {batch_count}")
                self.db.rollback()
                files_failed += len(batch)

        logger.info(
            f"Metadata backfill complete: "
            f"{files_processed} processed, "
            f"{files_skipped} skipped, "
            f"{files_failed} failed out of {total_files} total"
        )

        return {
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "files_failed": files_failed,
            "total_files": total_files,
        }

    def _update_file_metadata(
        self, file_entry: FileInventory, compute_checksum: bool = True
    ) -> str:
        """
        Update metadata for a single file.

        Args:
            file_entry: FileInventory database entry
            compute_checksum: Whether to compute checksum

        Returns:
            "updated", "skipped", or "failed"
        """
        file_path = Path(file_entry.file_path)

        # Check if file exists
        if not file_path.exists():
            logger.debug(f"File does not exist, skipping: {file_path}")
            return "skipped"

        # Extract metadata
        try:
            extension, mime_type, checksum = FileMetadataExtractor.extract_metadata(file_path)

            # Update fields only if they're currently null
            updated = False

            if file_entry.file_extension is None and extension:
                file_entry.file_extension = extension
                updated = True

            if file_entry.mime_type is None and mime_type:
                file_entry.mime_type = mime_type
                updated = True

            if compute_checksum and file_entry.checksum is None and checksum:
                file_entry.checksum = checksum
                updated = True

            if updated:
                logger.debug(f"Updated metadata for: {file_path}")
                return "updated"
            return "skipped"

        except Exception:
            logger.exception(f"Failed to extract metadata for {file_path}")
            return "failed"

    def backfill_path(
        self, path_id: int, batch_size: int = 100, compute_checksum: bool = True
    ) -> Dict[str, int]:
        """
        Backfill metadata for files in a specific monitored path.

        Args:
            path_id: ID of the monitored path
            batch_size: Number of files to process per batch
            compute_checksum: Whether to compute checksums

        Returns:
            Dictionary with statistics
        """
        logger.info(f"Starting metadata backfill for path ID {path_id}...")

        # Find files for this path missing metadata
        files_needing_update = (
            self.db.query(FileInventory)
            .filter(
                FileInventory.path_id == path_id,
                or_(
                    FileInventory.file_extension.is_(None),
                    FileInventory.mime_type.is_(None),
                    FileInventory.checksum.is_(None) if compute_checksum else False,
                ),
            )
            .all()
        )

        total_files = len(files_needing_update)
        logger.info(f"Found {total_files} files in path {path_id} needing metadata updates")

        if total_files == 0:
            return {"files_processed": 0, "files_skipped": 0, "files_failed": 0, "total_files": 0}

        files_processed = 0
        files_skipped = 0
        files_failed = 0
        batch_count = 0

        # Process in batches
        for i in range(0, total_files, batch_size):
            batch = files_needing_update[i : i + batch_size]
            batch_count += 1

            for file_entry in batch:
                try:
                    result = self._update_file_metadata(
                        file_entry, compute_checksum=compute_checksum
                    )

                    if result == "updated":
                        files_processed += 1
                    elif result == "skipped":
                        files_skipped += 1
                    else:
                        files_failed += 1

                except Exception:
                    logger.exception(f"Error processing file {file_entry.file_path}")
                    files_failed += 1

            # Commit after each batch
            try:
                self.db.commit()
                logger.info(
                    f"Batch {batch_count} complete: "
                    f"Processed {files_processed}/{total_files} files"
                )
            except Exception:
                logger.exception(f"Error committing batch {batch_count}")
                self.db.rollback()
                files_failed += len(batch)

        logger.info(
            f"Path {path_id} metadata backfill complete: "
            f"{files_processed} processed, "
            f"{files_skipped} skipped, "
            f"{files_failed} failed"
        )

        return {
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "files_failed": files_failed,
            "total_files": total_files,
        }
