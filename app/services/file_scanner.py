"""File scanning service."""
import os
import logging
import hashlib
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import MonitoredPath, Criteria, PinnedFile, FileInventory, StorageType, FileStatus
from app.services.criteria_matcher import CriteriaMatcher

logger = logging.getLogger(__name__)


class FileScanner:
    """Scans directories for files matching criteria."""
    
    @staticmethod
    def scan_path(path: MonitoredPath, db: Optional[Session] = None) -> dict:
        """
        Scan a monitored path for files matching criteria and update file inventory.

        Args:
            path: The monitored path to scan
            db: Database session for inventory management and pinned files

        Returns:
            dict with:
            - 'to_cold': List of (file_path, matched_criteria_ids) tuples for files to move to cold storage
            - 'to_hot': List of (symlink_path, cold_storage_path) tuples for files to move back to hot storage
            - 'inventory_updated': Number of inventory entries updated
        """
        matching_files = []
        files_to_thaw = []  # Symlinks pointing to cold storage where file doesn't match
        source_path = Path(path.source_path)
        dest_base = Path(path.cold_storage_path)
        
        logger.debug(f"Scanning path: {path.name} (ID: {path.id})")
        logger.debug(f"  Source: {source_path}")
        logger.debug(f"  Cold Storage: {path.cold_storage_path}")
        logger.debug(f"  Operation: {path.operation_type.value}")
        
        if not source_path.exists() or not source_path.is_dir():
            logger.warning(f"Path {path.name}: Source path does not exist or is not a directory: {source_path}")
            return {"to_cold": [], "to_hot": []}
        
        # Get all criteria for this path
        criteria = path.criteria
        enabled_criteria = [c for c in criteria if c.enabled]
        logger.debug(f"Path {path.name}: {len(enabled_criteria)} enabled criteria out of {len(criteria)} total")
        for criterion in enabled_criteria:
            logger.debug(f"  - Criterion {criterion.id}: {criterion.criterion_type.value} {criterion.operator.value} {criterion.value}")
        
        # Get list of pinned files if db is provided
        pinned_paths = set()
        if db:
            pinned = db.query(PinnedFile).filter(
                PinnedFile.path_id == path.id
            ).all()
            pinned_paths = {Path(p.file_path) for p in pinned}
            if pinned_paths:
                logger.debug(f"Path {path.name}: {len(pinned_paths)} pinned files will be skipped")
        
        file_count = 0
        # Walk through directory recursively
        # Note: os.walk() by default does NOT follow symlinks to directories (followlinks=False)
        # This is intentional for security, but means symlinked directories won't be scanned
        # Convert Path to string for os.walk() compatibility
        source_path_str = str(source_path.resolve())
        logger.debug(f"Path {path.name}: Starting recursive directory walk from {source_path_str}")
        for root, dirs, files in os.walk(source_path_str, followlinks=False):
            logger.debug(f"Path {path.name}: Walking directory: {root} (found {len(files)} files, {len(dirs)} subdirectories)")
            # Skip hidden directories (modifying dirs in-place prevents os.walk from descending into them)
            original_dir_count = len(dirs)
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            if len(dirs) < original_dir_count:
                logger.debug(f"Path {path.name}: Skipped {original_dir_count - len(dirs)} hidden directories in {root}")
            
            for filename in files:
                # Skip hidden files
                if filename.startswith('.'):
                    continue
                
                file_path = Path(root) / filename
                file_count += 1
                
                # Skip pinned files
                if file_path in pinned_paths:
                    logger.debug(f"File {file_path}: SKIPPED (pinned)")
                    continue
                
                # If this is a symlink, resolve it to check the actual file in cold storage
                actual_file_path = None
                is_symlink_to_cold = False
                if file_path.is_symlink():
                    try:
                        resolved = file_path.resolve(strict=True)
                        # Check if the symlink points to cold storage
                        try:
                            # Check if resolved path is under the cold storage base
                            resolved.relative_to(dest_base)
                            is_symlink_to_cold = True
                        except ValueError:
                            # Not in cold storage
                            pass
                        
                        # If the resolved path is in cold storage, use it for criteria checking
                        # This ensures we check the actual file's metadata, not the symlink's
                        actual_file_path = resolved
                        logger.debug(f"File {file_path}: Is symlink, will check actual file at {resolved}")
                        if is_symlink_to_cold:
                            logger.debug(f"File {file_path}: Symlink points to cold storage")
                    except (OSError, RuntimeError) as e:
                        # If resolution fails, skip this file
                        logger.debug(f"File {file_path}: Symlink resolution failed - {e}")
                        continue
                
                try:
                    matches, matched_ids = CriteriaMatcher.match_file(file_path, criteria, actual_file_path)
                    if matches:
                        logger.debug(f"File {file_path}: MATCHED - will be moved")
                        matching_files.append((file_path, matched_ids))
                    else:
                        logger.debug(f"File {file_path}: NOT MATCHED - will not be moved")
                        # If this is a symlink pointing to cold storage and it doesn't match,
                        # we need to move the file back to hot storage
                        if is_symlink_to_cold and actual_file_path:
                            logger.debug(f"File {file_path}: Symlink to cold storage doesn't match - will move back to hot storage")
                            files_to_thaw.append((file_path, actual_file_path))
                except (OSError, PermissionError) as e:
                    # Skip files we can't access
                    logger.debug(f"File {file_path}: SKIPPED (access error: {e})")
                    continue
        
        logger.debug(f"Path {path.name}: Scanned {file_count} files, {len(matching_files)} matched criteria, {len(files_to_thaw)} need to be moved back")

        # Update file inventory for both hot and cold storage
        inventory_updated = 0
        if db:
            inventory_updated = FileScanner._update_file_inventory(path, db)

        return {
            "to_cold": matching_files,
            "to_hot": files_to_thaw,
            "inventory_updated": inventory_updated
        }

    @staticmethod
    def _update_file_inventory(path: MonitoredPath, db: Session) -> int:
        """
        Update file inventory for both hot and cold storage.

        Returns:
            Number of inventory entries updated/created
        """
        updated_count = 0

        # Scan hot storage
        hot_files = FileScanner._scan_directory(path.source_path, StorageType.HOT)
        updated_count += FileScanner._update_inventory_for_storage(path, hot_files, StorageType.HOT, db)

        # Scan cold storage
        cold_files = FileScanner._scan_directory(path.cold_storage_path, StorageType.COLD)
        updated_count += FileScanner._update_inventory_for_storage(path, cold_files, StorageType.COLD, db)

        # Mark files not seen recently as missing
        cutoff_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)  # Files not seen today
        missing_count = db.query(FileInventory).filter(
            FileInventory.path_id == path.id,
            FileInventory.last_seen < cutoff_time,
            FileInventory.status == FileStatus.ACTIVE
        ).update({"status": FileStatus.MISSING})
        updated_count += missing_count

        if missing_count > 0:
            logger.info(f"Path {path.name}: Marked {missing_count} files as missing")

        db.commit()
        logger.debug(f"Path {path.name}: Updated {updated_count} inventory entries")
        return updated_count

    @staticmethod
    def _scan_directory(directory_path: str, storage_type: StorageType) -> List[Dict]:
        """
        Scan a directory and return file information.

        Returns:
            List of dicts with file info: {'path': str, 'size': int, 'mtime': datetime, 'checksum': str}
        """
        files = []
        directory = Path(directory_path)

        if not directory.exists() or not directory.is_dir():
            logger.warning(f"Directory does not exist or is not a directory: {directory_path}")
            return files

        for root, dirs, files_in_dir in os.walk(str(directory), followlinks=False):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for filename in files_in_dir:
                # Skip hidden files
                if filename.startswith('.'):
                    continue

                file_path = Path(root) / filename
                try:
                    stat_info = file_path.stat()
                    file_info = {
                        'path': str(file_path),
                        'size': stat_info.st_size,
                        'mtime': datetime.fromtimestamp(stat_info.st_mtime),
                        'checksum': None  # We'll compute this if needed
                    }
                    files.append(file_info)
                except (OSError, PermissionError) as e:
                    logger.debug(f"Could not stat file {file_path}: {e}")
                    continue

        return files

    @staticmethod
    def _update_inventory_for_storage(path: MonitoredPath, files: List[Dict],
                                   storage_type: StorageType, db: Session) -> int:
        """
        Update inventory entries for files in a specific storage location.

        Returns:
            Number of entries updated/created
        """
        updated_count = 0

        for file_info in files:
            file_path = file_info['path']

            # Check if inventory entry exists
            inventory_entry = db.query(FileInventory).filter(
                FileInventory.path_id == path.id,
                FileInventory.file_path == file_path
            ).first()

            if inventory_entry:
                # Update existing entry
                if (inventory_entry.file_size != file_info['size'] or
                    inventory_entry.file_mtime != file_info['mtime'] or
                    inventory_entry.status != FileStatus.ACTIVE):
                    inventory_entry.file_size = file_info['size']
                    inventory_entry.file_mtime = file_info['mtime']
                    inventory_entry.status = FileStatus.ACTIVE
                    inventory_entry.storage_type = storage_type
                    updated_count += 1
            else:
                # Create new entry
                new_entry = FileInventory(
                    path_id=path.id,
                    file_path=file_path,
                    storage_type=storage_type,
                    file_size=file_info['size'],
                    file_mtime=file_info['mtime'],
                    status=FileStatus.ACTIVE
                )
                db.add(new_entry)
                updated_count += 1

        return updated_count

