"""File reconciliation service - recovers missing symlinks and validates file integrity."""

import logging
from pathlib import Path
from typing import Dict

from sqlalchemy.orm import Session

from app.models import FileInventory, MonitoredPath, OperationType, StorageType

logger = logging.getLogger(__name__)


class FileReconciliation:
    """Handles file reconciliation tasks like recovering missing symlinks."""

    @staticmethod
    def reconcile_missing_symlinks(path: MonitoredPath, db: Session) -> Dict:
        """
        Reconcile missing symlinks for files in cold storage.

        Scenario: Administrator accidentally deleted symlinks in hot storage,
        but the actual files still exist in cold storage. This method recreates
        the missing symlinks.

        Process:
        1. Query FileInventory for files with storage_type="cold"
        2. For each cold storage file, check if it should have a symlink in hot storage
        3. If symlink is missing, recreate it

        Args:
            path: The monitored path to reconcile
            db: Database session

        Returns:
            Dictionary with reconciliation statistics
        """
        stats = {
            "symlinks_checked": 0,
            "symlinks_created": 0,
            "symlinks_skipped": 0,  # Already exist
            "errors": [],
        }

        # Only reconcile symlinks if operation type is symlink
        if path.operation_type != OperationType.SYMLINK:
            logger.debug(
                f"Skipping symlink reconciliation for {path.name} - operation type is {path.operation_type.value}"
            )
            return stats

        logger.info(f"Starting symlink reconciliation for path: {path.name}")

        source_base = Path(path.source_path)
        dest_base = Path(path.cold_storage_path)

        # Get all files in cold storage from inventory
        cold_files = (
            db.query(FileInventory)
            .filter(
                FileInventory.path_id == path.id,
                FileInventory.storage_type == StorageType.COLD,
                FileInventory.status == "active",
            )
            .all()
        )

        logger.info(f"Found {len(cold_files)} files in cold storage inventory")

        # Existence caches to avoid redundant filesystem calls
        dir_exists_cache = {}
        file_exists_cache = {}

        def check_path_exists(p: Path) -> bool:
            p_str = str(p)
            if p_str in file_exists_cache:
                return file_exists_cache[p_str]

            if p.exists():
                file_exists_cache[p_str] = True
                return True

            # If it doesn't exist, check if its parent exists (optimization for missing folders)
            parent = p.parent
            parent_str = str(parent)
            if parent_str in dir_exists_cache and not dir_exists_cache[parent_str]:
                file_exists_cache[p_str] = False
                return False

            parent_exists = parent.exists()
            dir_exists_cache[parent_str] = parent_exists
            file_exists_cache[p_str] = False
            return False

        for inventory_entry in cold_files:
            stats["symlinks_checked"] += 1
            cold_file_path = Path(inventory_entry.file_path)

            # Determine where the symlink should be in hot storage
            try:
                # If the file is in cold storage directory, calculate corresponding hot path
                relative_path = cold_file_path.relative_to(dest_base)
                expected_symlink_path = source_base / relative_path
            except ValueError:
                # File path is not under cold storage base
                try:
                    cold_file_path.relative_to(source_path)
                    # File is already in hot storage, skip
                    stats["symlinks_skipped"] += 1
                    continue
                except ValueError:
                    logger.warning(f"File path not in hot or cold storage: {cold_file_path}")
                    continue

            # Check if symlink exists
            if expected_symlink_path.exists():
                # Verify it's a symlink pointing to the right place
                if expected_symlink_path.is_symlink():
                    try:
                        target = expected_symlink_path.resolve(strict=True)
                        if target == cold_file_path:
                            stats["symlinks_skipped"] += 1
                            continue

                        logger.warning(
                            f"Symlink points to wrong location: {expected_symlink_path} "
                            f"-> {target} (expected {cold_file_path})"
                        )
                        stats["symlinks_skipped"] += 1
                        continue
                    except (OSError, RuntimeError):
                        # Broken symlink - will be recreated below
                        logger.info(f"Broken symlink found, will recreate: {expected_symlink_path}")
                        expected_symlink_path.unlink()
                else:
                    # Path exists but is not a symlink - skip to avoid data loss
                    logger.warning(
                        f"Cannot create symlink at {expected_symlink_path} - "
                        f"path exists and is not a symlink"
                    )
                    stats["symlinks_skipped"] += 1
                    continue

            # Create the missing symlink
            try:
                # Verify the cold storage file actually exists (using cache)
                if not check_path_exists(cold_file_path):
                    logger.warning(f"Cold storage file does not exist: {cold_file_path}")
                    stats["errors"].append(f"Cold storage file missing: {cold_file_path}")
                    continue

                # Ensure parent directory exists
                expected_symlink_path.parent.mkdir(parents=True, exist_ok=True)

                # Create symlink pointing to cold storage file
                expected_symlink_path.symlink_to(cold_file_path)

                stats["symlinks_created"] += 1
                logger.info(
                    f"Recreated missing symlink: {expected_symlink_path} -> {cold_file_path}"
                )

            except Exception as e:
                error_msg = (
                    f"Failed to create symlink {expected_symlink_path} -> {cold_file_path}: {e!s}"
                )
                logger.exception(error_msg)
                stats["errors"].append(error_msg)

        logger.info(
            f"Symlink reconciliation complete for {path.name}: "
            f"Checked {stats['symlinks_checked']}, "
            f"Created {stats['symlinks_created']}, "
            f"Skipped {stats['symlinks_skipped']}, "
            f"Errors: {len(stats['errors'])}"
        )

        return stats

    @staticmethod
    def verify_cold_storage_tracking(path: MonitoredPath, db: Session) -> Dict:
        """
        Verify that all files in cold storage are tracked in the database.
        This is primarily for validation - the regular scan should already handle this.

        Args:
            path: The monitored path to verify
            db: Database session

        Returns:
            Dictionary with verification statistics
        """
        stats = {"files_checked": 0, "files_tracked": 0, "files_untracked": 0}

        dest_base = Path(path.cold_storage_path)

        if not dest_base.exists():
            logger.warning(f"Cold storage path does not exist: {dest_base}")
            return stats

        # Scan cold storage directory
        for file_path in dest_base.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                stats["files_checked"] += 1

                # Check if file is in inventory
                inventory_entry = (
                    db.query(FileInventory)
                    .filter(
                        FileInventory.path_id == path.id, FileInventory.file_path == str(file_path)
                    )
                    .first()
                )

                if inventory_entry:
                    stats["files_tracked"] += 1
                else:
                    stats["files_untracked"] += 1
                    logger.info(f"Untracked file in cold storage: {file_path}")

        return stats
