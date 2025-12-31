"""File scanning service."""
import os
import logging
from pathlib import Path
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session
from app.models import MonitoredPath, Criteria, PinnedFile
from app.services.criteria_matcher import CriteriaMatcher

logger = logging.getLogger(__name__)


class FileScanner:
    """Scans directories for files matching criteria."""
    
    @staticmethod
    def scan_path(path: MonitoredPath, db: Optional[Session] = None) -> dict:
        """
        Scan a monitored path for files matching criteria.
        
        Args:
            path: The monitored path to scan
            db: Database session to check for pinned files
        
        Returns:
            dict with:
            - 'to_cold': List of (file_path, matched_criteria_ids) tuples for files to move to cold storage
            - 'to_hot': List of (symlink_path, cold_storage_path) tuples for files to move back to hot storage
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
        return {"to_cold": matching_files, "to_hot": files_to_thaw}

