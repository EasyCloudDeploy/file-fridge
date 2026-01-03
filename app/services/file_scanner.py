"""File scanning service - optimized for macOS and Linux network mounts."""
import os
import logging
import fnmatch
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Iterator
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import MonitoredPath, Criteria, PinnedFile, FileInventory, StorageType, FileStatus, CriterionType
from app.services.criteria_matcher import CriteriaMatcher
from app.utils.network_detection import check_atime_availability

logger = logging.getLogger(__name__)

class FileScanner:
    """Scans directories for files matching criteria using high-performance scandir."""
    
    # Metadata files to ignore to prevent false access triggers on network mounts
    IGNORED_PATTERNS = {
        ".DS_Store", "._*", ".Spotlight-V100", ".Trashes", 
        ".fseventsd", ".TemporaryItems", "desktop.ini", "thumbs.db"
    }

    @staticmethod
    def scan_path(path: MonitoredPath, db: Optional[Session] = None) -> dict:
        """
        Scan a monitored path for files matching criteria and update file inventory.
        """
        matching_files = []
        files_to_thaw = []
        files_skipped_hot = 0  # Files correctly in hot storage
        files_skipped_cold = 0  # Files correctly in cold storage
        source_path = Path(path.source_path)
        dest_base = Path(path.cold_storage_path)

        logger.debug(f"Starting scan: {path.name} (ID: {path.id})")

        if not source_path.exists() or not source_path.is_dir():
            logger.warning(f"Path {path.name}: Source path unreachable: {source_path}")
            return {"to_cold": [], "to_hot": [], "inventory_updated": 0, "skipped_hot": 0, "skipped_cold": 0}

        # 1. Validate Criteria vs Filesystem (atime check)
        enabled_criteria = [c for c in path.criteria if c.enabled]
        if db:
            atime_used = any(c.criterion_type == CriterionType.ATIME for c in enabled_criteria)
            if atime_used:
                atime_available, error_msg = check_atime_availability(path.cold_storage_path)
                if not atime_available:
                    path.error_message = error_msg
                    db.commit()
                    logger.error(f"Scan aborted for {path.name}: {error_msg}")
                    return {"to_cold": [], "to_hot": [], "inventory_updated": 0, "skipped_hot": 0, "skipped_cold": 0}
                elif path.error_message:
                    path.error_message = None
                    db.commit()

        # Validate scan interval vs time-based criteria
        if enabled_criteria:
            scan_interval_minutes = path.check_interval_seconds / 60
            for criterion in enabled_criteria:
                if criterion.criterion_type in [CriterionType.ATIME, CriterionType.MTIME, CriterionType.CTIME]:
                    try:
                        threshold_minutes = float(criterion.value)
                        # Warn if scan interval is more than 3x the threshold
                        if scan_interval_minutes > threshold_minutes * 3:
                            logger.warning(
                                f"Path {path.name}: Scan interval ({scan_interval_minutes:.0f} min) is {scan_interval_minutes/threshold_minutes:.1f}x larger than "
                                f"{criterion.criterion_type.value} threshold ({threshold_minutes:.0f} min). "
                                f"Files may age significantly between scans, reducing effectiveness. "
                                f"Consider reducing scan interval to ~{threshold_minutes:.0f} min or less."
                            )
                    except (ValueError, TypeError):
                        pass

        # 2. Load Pinned Files
        pinned_paths = set()
        if db:
            pinned = db.query(PinnedFile).filter(PinnedFile.path_id == path.id).all()
            pinned_paths = {Path(p.file_path) for p in pinned}

        # 3. Perform Recursive Scan - HOT STORAGE
        file_count = 0
        for entry in FileScanner._recursive_scandir(source_path):
            file_path = Path(entry.path)
            file_count += 1

            if file_path in pinned_paths:
                continue

            # Handle Symlinks (Potential Cold Storage Files)
            actual_file_path = None
            is_symlink_to_cold = False

            if entry.is_symlink():
                try:
                    resolved = file_path.resolve(strict=True)
                    actual_file_path = resolved
                    # Check if target is in the configured cold storage
                    try:
                        resolved.relative_to(dest_base)
                        is_symlink_to_cold = True
                    except ValueError:
                        pass # Symlink points elsewhere, process as normal file
                except (OSError, RuntimeError):
                    continue # Broken symlink

            # 4. Evaluation Logic - HOT STORAGE
            try:
                # is_active is True if the file matches the criteria (should be kept in HOT storage)
                # Criteria define what files to KEEP active/hot, not what to move to cold
                # Example: "atime < 3" means "keep files accessed in last 3 minutes in hot storage"
                # Simple, direct criteria evaluation without hysteresis
                is_active, matched_ids = CriteriaMatcher.match_file(
                    file_path, path.criteria, actual_file_path
                )

                if is_active:
                    # File matches criteria (is ACTIVE/HOT)
                    if is_symlink_to_cold and actual_file_path:
                        # File is active but currently in COLD storage -> THAW back to HOT
                        logger.info(f"File {file_path}: Active file in cold storage -> THAWING")
                        files_to_thaw.append((file_path, actual_file_path))
                    else:
                        # File is active and in HOT storage -> CORRECTLY PLACED, SKIP
                        # No action needed, file is where it should be
                        files_skipped_hot += 1
                        continue  # Skip to next file, no further processing needed
                else:
                    # File does NOT match criteria (is INACTIVE/OLD)
                    if not is_symlink_to_cold:
                        # File is inactive but still in HOT storage -> MOVE TO COLD
                        logger.info(f"File {file_path}: Inactive file, moving to cold storage")
                        matching_files.append((file_path, matched_ids))
                    else:
                        # File is inactive and already in COLD -> CORRECTLY PLACED, SKIP
                        # No action needed, file is where it should be
                        files_skipped_cold += 1
                        continue  # Skip to next file, no further processing needed

            except (OSError, PermissionError) as e:
                logger.debug(f"Access error for {file_path}: {e}")
                continue

        # 4.5. SCAN COLD STORAGE DIRECTLY (for MOVE operations)
        # This is needed to detect files that were moved to cold storage (no symlink left behind)
        # and have been updated since being moved. Such files should be moved back to hot storage.
        if dest_base.exists() and dest_base.is_dir():
            logger.debug(f"Scanning cold storage directly: {dest_base}")
            for entry in FileScanner._recursive_scandir(dest_base):
                cold_file_path = Path(entry.path)
                file_count += 1

                # Calculate the corresponding hot storage path
                try:
                    relative_path = cold_file_path.relative_to(dest_base)
                    hot_file_path = source_path / relative_path
                except ValueError:
                    logger.debug(f"Could not calculate hot path for {cold_file_path}")
                    continue

                # Skip if there's already a file or symlink at the hot location
                # (already handled in hot storage scan above)
                if hot_file_path.exists():
                    continue

                # Check if file is pinned
                if cold_file_path in pinned_paths or hot_file_path in pinned_paths:
                    continue

                # Evaluate if this cold storage file should be in hot storage
                try:
                    # Check criteria against the actual cold storage file
                    is_active, matched_ids = CriteriaMatcher.match_file(
                        hot_file_path, path.criteria, cold_file_path
                    )

                    if is_active:
                        # File in cold storage matches active criteria -> should be in hot storage
                        logger.info(f"File {cold_file_path}: Active file in cold storage (MOVE operation) -> THAWING")
                        files_to_thaw.append((hot_file_path, cold_file_path))
                    else:
                        # File is inactive and in cold storage -> correctly placed
                        files_skipped_cold += 1

                except (OSError, PermissionError) as e:
                    logger.debug(f"Access error for {cold_file_path}: {e}")
                    continue

        # 5. Inventory Management
        inventory_updated = 0
        if db:
            inventory_updated = FileScanner._update_file_inventory(path, db)

        # Log scan summary
        total_scanned = file_count
        total_actions = len(matching_files) + len(files_to_thaw)
        total_skipped = files_skipped_hot + files_skipped_cold
        logger.info(
            f"Scan complete for {path.name}: "
            f"Scanned {total_scanned} files, "
            f"{len(matching_files)} to cold, "
            f"{len(files_to_thaw)} to hot, "
            f"{total_skipped} correctly placed ({files_skipped_hot} hot, {files_skipped_cold} cold)"
        )

        return {
            "to_cold": matching_files,
            "to_hot": files_to_thaw,
            "inventory_updated": inventory_updated,
            "skipped_hot": files_skipped_hot,
            "skipped_cold": files_skipped_cold,
            "total_scanned": total_scanned
        }

    @staticmethod
    def _recursive_scandir(path: Path) -> Iterator[os.DirEntry]:
        """Generator implementation of os.scandir for high performance."""
        try:
            with os.scandir(str(path)) as it:
                for entry in it:
                    # Filter out hidden files and macOS metadata
                    if entry.name.startswith('.'):
                        continue
                    if any(fnmatch.fnmatch(entry.name, p) for p in FileScanner.IGNORED_PATTERNS):
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        yield from FileScanner._recursive_scandir(Path(entry.path))
                    else:
                        yield entry
        except (OSError, PermissionError):
            pass

    @staticmethod
    def _update_file_inventory(path: MonitoredPath, db: Session) -> int:
        """Updates the database inventory for both storage tiers."""
        updated_count = 0
        
        # Sync Hot Tier
        hot_files = FileScanner._scan_flat_list(path.source_path)
        updated_count += FileScanner._update_db_entries(path, hot_files, StorageType.HOT, db)

        # Sync Cold Tier
        cold_files = FileScanner._scan_flat_list(path.cold_storage_path)
        updated_count += FileScanner._update_db_entries(path, cold_files, StorageType.COLD, db)

        # Mark "Ghost" files as missing
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        missing = db.query(FileInventory).filter(
            FileInventory.path_id == path.id,
            FileInventory.last_seen < cutoff,
            FileInventory.status == FileStatus.ACTIVE
        ).update({"status": FileStatus.MISSING})
        
        db.commit()
        return updated_count + missing

    @staticmethod
    def _scan_flat_list(directory_path: str) -> List[Dict]:
        """Helper to get metadata for inventory updates."""
        results = []
        if not os.path.exists(directory_path):
            return results

        for entry in FileScanner._recursive_scandir(Path(directory_path)):
            try:
                stat = entry.stat(follow_symlinks=False)

                # Check if this is a symlink - if so, we need to know where it points
                is_symlink = Path(entry.path).is_symlink()

                results.append({
                    'path': entry.path,
                    'size': stat.st_size,
                    'mtime': datetime.fromtimestamp(stat.st_mtime),
                    'atime': datetime.fromtimestamp(stat.st_atime),
                    'ctime': datetime.fromtimestamp(stat.st_ctime),
                    'is_symlink': is_symlink
                })
            except OSError:
                continue
        return results

    @staticmethod
    def _update_db_entries(path: MonitoredPath, files: List[Dict], tier: StorageType, db: Session) -> int:
        """Synchronizes file metadata with the database FileInventory table."""
        from app.services.tag_rule_service import TagRuleService
        from app.services.file_metadata import FileMetadataExtractor
        from pathlib import Path as PathLib

        count = 0
        tag_rule_service = TagRuleService(db)
        new_files = []
        updated_files = []

        for info in files:
            # Determine the actual storage tier
            # If this is a symlink in hot storage, the actual file is in cold storage
            actual_tier = tier
            if tier == StorageType.HOT and info.get('is_symlink', False):
                # Symlinks in hot storage point to cold storage
                actual_tier = StorageType.COLD

            entry = db.query(FileInventory).filter(
                FileInventory.path_id == path.id,
                FileInventory.file_path == info['path']
            ).first()

            if entry:
                # Update existing entry
                updated = False
                if entry.file_size != info['size'] or entry.status != FileStatus.ACTIVE or entry.storage_type != actual_tier:
                    entry.file_size = info['size']
                    entry.file_mtime = info['mtime']
                    entry.file_atime = info['atime']
                    entry.file_ctime = info['ctime']
                    entry.status = FileStatus.ACTIVE
                    entry.storage_type = actual_tier
                    updated = True

                # Check if metadata is missing and populate it
                if entry.file_extension is None or entry.mime_type is None:
                    try:
                        file_path = PathLib(info['path'])
                        if file_path.exists():
                            extension, mime_type, checksum = FileMetadataExtractor.extract_metadata(file_path)
                            if entry.file_extension is None and extension:
                                entry.file_extension = extension
                                updated = True
                            if entry.mime_type is None and mime_type:
                                entry.mime_type = mime_type
                                updated = True
                            # Only compute checksum if file is small enough
                            if entry.checksum is None and checksum and info['size'] < 1024 * 1024 * 100:  # 100MB limit
                                entry.checksum = checksum
                                updated = True
                    except Exception as e:
                        logger.debug(f"Could not extract metadata for {info['path']}: {e}")

                if updated:
                    updated_files.append(entry)
                    count += 1
            else:
                # Create new entry with metadata
                extension = None
                mime_type = None
                checksum = None

                try:
                    file_path = PathLib(info['path'])
                    if file_path.exists():
                        extension, mime_type, checksum = FileMetadataExtractor.extract_metadata(file_path)
                except Exception as e:
                    logger.debug(f"Could not extract metadata for {info['path']}: {e}")

                new_entry = FileInventory(
                    path_id=path.id, file_path=info['path'],
                    storage_type=actual_tier, file_size=info['size'],
                    file_mtime=info['mtime'], file_atime=info['atime'],
                    file_ctime=info['ctime'], status=FileStatus.ACTIVE,
                    file_extension=extension, mime_type=mime_type, checksum=checksum
                )
                db.add(new_entry)
                new_files.append(new_entry)
                count += 1

        # Commit to ensure new files have IDs
        if new_files or updated_files:
            db.commit()

            # Apply tag rules to newly added files
            for file_entry in new_files:
                db.refresh(file_entry)  # Ensure we have the ID
                try:
                    tag_rule_service.apply_rules_to_file(file_entry)
                except Exception as e:
                    logger.error(f"Error applying tag rules to file {file_entry.file_path}: {e}")

            # Also apply tag rules to updated files that now have metadata
            for file_entry in updated_files:
                try:
                    tag_rule_service.apply_rules_to_file(file_entry)
                except Exception as e:
                    logger.error(f"Error applying tag rules to file {file_entry.file_path}: {e}")

        return count