"""Unified file workflow service - scanning, moving, and inventory management."""

import fnmatch
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Dict, Iterator, List, Optional, Set

from sqlalchemy.orm import Session, sessionmaker

from app.database import engine
from app.models import (
    CriterionType,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    PinnedFile,
    ScanStatus,
    StorageType,
)
from app.services.audit_trail_service import audit_trail_service
from app.services.checksum_verifier import checksum_verifier
from app.services.criteria_matcher import CriteriaMatcher
from app.services.file_cleanup import FileCleanup
from app.services.file_mover import FileMover
from app.services.file_reconciliation import FileReconciliation
from app.services.scan_progress import scan_progress_manager
from app.services.storage_routing_service import storage_routing_service
from app.utils.network_detection import check_atime_availability

logger = logging.getLogger(__name__)

# Thread-local session factory for concurrent database access
SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class FileWorkflowService:
    """Unified service for file scanning, movement, and inventory management."""

    # Metadata files to ignore
    IGNORED_PATTERNS: ClassVar[Set[str]] = {
        ".DS_Store",
        "._*",
        ".Spotlight-V100",
        ".Trashes",
        ".fseventsd",
        ".TemporaryItems",
        "desktop.ini",
        "thumbs.db",
    }

    def process_path(self, path: MonitoredPath, db: Session) -> dict:
        """
        Process a monitored path: scan, match, and move files.

        Returns:
            dict with scan results including:
            - scan_skipped: True if scan was skipped because one is already running
        """
        scan_id, scan_started = scan_progress_manager.start_scan(path.id, total_files=0)

        if not scan_started:
            logger.warning(f"Scan already running for path {path.id}, skipping")
            return {
                "path_id": path.id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "errors": [],
                "scan_skipped": True,
                "scan_skipped_reason": "A scan is already running for this path",
            }

        logger.info(f"Started scan {scan_id} for path {path.id}")

        # Mark scan as pending
        path.last_scan_status = ScanStatus.PENDING
        db.commit()

        try:
            if path.error_message:
                logger.warning(
                    f"Path {path.name} (ID: {path.id}) is in error state: {path.error_message}"
                )
                scan_progress_manager.finish_scan(path.id, status="failed")
                # Update scan status in database
                error_log = f"Path is in error state: {path.error_message}"
                path.last_scan_at = datetime.now(tz=timezone.utc)
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = error_log
                db.commit()
                return {
                    "path_id": path.id,
                    "files_found": 0,
                    "files_moved": 0,
                    "files_cleaned": 0,
                    "errors": [error_log],
                }

            results = {
                "path_id": path.id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "files_skipped": 0,
                "total_scanned": 0,
                "errors": [],
            }

            # Cleanup phase
            try:
                cleanup_results = FileCleanup.cleanup_missing_files(db, path_id=path.id)
                results["files_cleaned"] = cleanup_results["removed"]
                if cleanup_results["errors"]:
                    results["errors"].extend(cleanup_results["errors"])

                duplicate_results = FileCleanup.cleanup_duplicates(db, path_id=path.id)
                results["files_cleaned"] += duplicate_results["removed"]
                if duplicate_results["errors"]:
                    results["errors"].extend(duplicate_results["errors"])

                # Clean up symlink entries from inventory
                symlink_results = FileCleanup.cleanup_symlink_inventory_entries(db, path_id=path.id)
                results["files_cleaned"] += symlink_results["removed"]
                if symlink_results["errors"]:
                    results["errors"].extend(symlink_results["errors"])
            except Exception as e:
                logger.warning(f"Error during cleanup for path {path.id}: {e!s}")

            try:
                # Scan phase
                scan_results = self._scan_path(path, db)
                matching_files = scan_results["to_cold"]
                files_to_thaw = scan_results["to_hot"]
                results["files_found"] = len(matching_files)
                results["files_skipped"] = scan_results.get("skipped_hot", 0) + scan_results.get(
                    "skipped_cold", 0
                )
                results["total_scanned"] = scan_results.get("total_scanned", 0)

                total_files_to_process = len(matching_files) + len(files_to_thaw)
                scan_progress_manager.update_total_files(path.id, total_files_to_process)

                # Process thawing
                if files_to_thaw:
                    logger.info(f"Processing {len(files_to_thaw)} files to thaw")
                    max_workers = min(2, len(files_to_thaw))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_thaw = {
                            executor.submit(
                                self._thaw_single_file, symlink_path, cold_path, path
                            ): (
                                symlink_path,
                                cold_path,
                            )
                            for symlink_path, cold_path in files_to_thaw
                        }
                        for future in as_completed(future_to_thaw):
                            _symlink_path, cold_path = future_to_thaw[future]
                            try:
                                thaw_result = future.result()
                                if thaw_result["success"]:
                                    results["files_moved"] += 1
                                else:
                                    results["errors"].append(thaw_result["error"])
                            except Exception as e:
                                results["errors"].append(f"Exception thawing {cold_path}: {e!s}")

                # Process moves to cold storage
                if matching_files:
                    logger.info(f"Processing {len(matching_files)} files to cold storage")
                    max_workers = min(3, len(matching_files))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_file = {
                            executor.submit(
                                self._process_single_file, file_path, matched_ids, path
                            ): (file_path, matched_ids)
                            for file_path, matched_ids in matching_files
                        }
                        for future in as_completed(future_to_file):
                            file_path, _ = future_to_file[future]
                            try:
                                file_result = future.result()
                                if file_result["success"]:
                                    results["files_moved"] += 1
                                else:
                                    results["errors"].append(file_result["error"])
                            except Exception as e:
                                results["errors"].append(f"Exception processing {file_path}: {e!s}")

                # Reconciliation phase
                try:
                    reconciliation_stats = FileReconciliation.reconcile_missing_symlinks(path, db)
                    if reconciliation_stats["symlinks_created"] > 0:
                        logger.info(
                            f"Created {reconciliation_stats['symlinks_created']} missing symlinks"
                        )
                    if reconciliation_stats["errors"]:
                        results["errors"].extend(reconciliation_stats["errors"])
                except Exception as e:
                    results["errors"].append(f"Reconciliation error: {e!s}")

            except Exception as e:
                results["errors"].append(f"Error processing path {path.id}: {e!s}")
                scan_progress_manager.finish_scan(path.id, status="failed")
                # Update scan status in database
                path.last_scan_at = datetime.now(tz=timezone.utc)
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = "\n".join(results["errors"])
                db.commit()
                return results

            scan_progress_manager.finish_scan(path.id, status="completed")
            # Update scan status in database - success
            path.last_scan_at = datetime.now(tz=timezone.utc)
            if results["errors"]:
                # Partial success - completed but with some errors
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = "\n".join(results["errors"])
            else:
                path.last_scan_status = ScanStatus.SUCCESS
                path.last_scan_error_log = None
            db.commit()
            return results

        except Exception as e:
            logger.error(
                f"Unexpected error in process_path for path {path.id}: {e!s}", exc_info=True
            )
            scan_progress_manager.finish_scan(path.id, status="failed")
            # Update scan status in database
            error_log = f"Unexpected error: {e!s}"
            try:
                path.last_scan_at = datetime.now(tz=timezone.utc)
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = error_log
                db.commit()
            except Exception:
                # If we can't update the database, log and continue
                logger.warning(f"Could not update scan status for path {path.id}")
            return {
                "path_id": path.id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "errors": [error_log],
            }

    def _scan_path(self, path: MonitoredPath, db: Session) -> dict:
        """Scan a monitored path for files matching criteria."""
        scan_start_time = datetime.now(tz=timezone.utc)
        matching_files = []
        files_to_thaw = []
        files_skipped_hot = 0
        files_skipped_cold = 0
        hot_files_metadata = []
        cold_files_metadata = []

        source_path = Path(path.source_path)
        dest_base = Path(path.cold_storage_path)

        if not source_path.exists() or not source_path.is_dir():
            logger.warning(f"Path {path.name}: Source path unreachable: {source_path}")
            return {
                "to_cold": [],
                "to_hot": [],
                "inventory_updated": 0,
                "skipped_hot": 0,
                "skipped_cold": 0,
            }

        # Validate atime criteria
        enabled_criteria = [c for c in path.criteria if c.enabled]
        atime_used = any(c.criterion_type == CriterionType.ATIME for c in enabled_criteria)
        if atime_used:
            atime_available, error_msg = check_atime_availability(path.cold_storage_path)
            if not atime_available:
                path.error_message = error_msg
                db.commit()
                logger.error(f"Scan aborted for {path.name}: {error_msg}")
                return {
                    "to_cold": [],
                    "to_hot": [],
                    "inventory_updated": 0,
                    "skipped_hot": 0,
                    "skipped_cold": 0,
                }
            if path.error_message:
                path.error_message = None
                db.commit()

        # Load pinned files
        pinned = db.query(PinnedFile).filter(PinnedFile.path_id == path.id).all()
        pinned_paths = {Path(p.file_path) for p in pinned}

        # Scan hot storage
        file_count = 0
        for entry in self._recursive_scandir(source_path):
            file_path = Path(entry.path)
            file_count += 1

            stat_info = None
            try:
                stat_info = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            # Collect metadata for inventory sync
            is_symlink = entry.is_symlink()
            if not is_symlink:
                hot_files_metadata.append(
                    {
                        "path": entry.path,
                        "size": stat_info.st_size,
                        "mtime": datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc),
                        "atime": datetime.fromtimestamp(stat_info.st_atime, tz=timezone.utc),
                        "ctime": datetime.fromtimestamp(stat_info.st_ctime, tz=timezone.utc),
                    }
                )

            if file_path in pinned_paths:
                continue

            actual_file_path = None
            is_symlink_to_cold = False

            if is_symlink:
                try:
                    resolved = file_path.resolve(strict=True)
                    actual_file_path = resolved
                    try:
                        resolved.relative_to(dest_base)
                        is_symlink_to_cold = True
                    except ValueError:
                        pass
                except (OSError, RuntimeError):
                    continue

            try:
                is_active, matched_ids = CriteriaMatcher.match_file(
                    file_path, path.criteria, actual_file_path
                )

                if is_active:
                    if is_symlink_to_cold and actual_file_path:
                        files_to_thaw.append((file_path, actual_file_path))
                    else:
                        files_skipped_hot += 1
                elif not is_symlink_to_cold:
                    matching_files.append((file_path, matched_ids))
                else:
                    files_skipped_cold += 1
            except (OSError, PermissionError) as e:
                logger.debug(f"Access error for {file_path}: {e}")
                continue

        # Scan cold storage directly (for MOVE operations)
        if dest_base.exists() and dest_base.is_dir():
            for entry in self._recursive_scandir(dest_base):
                cold_file_path = Path(entry.path)
                file_count += 1

                stat_info = None
                try:
                    stat_info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue

                # Collect metadata for inventory sync
                if not entry.is_symlink():
                    cold_files_metadata.append(
                        {
                            "path": entry.path,
                            "size": stat_info.st_size,
                            "mtime": datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc),
                            "atime": datetime.fromtimestamp(stat_info.st_atime, tz=timezone.utc),
                            "ctime": datetime.fromtimestamp(stat_info.st_ctime, tz=timezone.utc),
                        }
                    )

                try:
                    relative_path = cold_file_path.relative_to(dest_base)
                    hot_file_path = source_path / relative_path
                except ValueError:
                    continue

                if hot_file_path.exists():
                    continue

                if cold_file_path in pinned_paths or hot_file_path in pinned_paths:
                    continue

                try:
                    is_active, _ = CriteriaMatcher.match_file(
                        hot_file_path, path.criteria, cold_file_path
                    )
                    if is_active:
                        files_to_thaw.append((hot_file_path, cold_file_path))
                    else:
                        files_skipped_cold += 1
                except (OSError, PermissionError):
                    continue

        # Update inventory using collected metadata (Avoid redundant walks!)
        inventory_updated = self._update_file_inventory(
            path,
            db,
            hot_files=hot_files_metadata,
            cold_files=cold_files_metadata,
            scan_start_time=scan_start_time,
        )

        return {
            "to_cold": matching_files,
            "to_hot": files_to_thaw,
            "inventory_updated": inventory_updated,
            "skipped_hot": files_skipped_hot,
            "skipped_cold": files_skipped_cold,
            "total_scanned": file_count,
        }

    def _process_single_file(
        self, file_path: Path, matched_criteria_ids: list, path: MonitoredPath
    ) -> dict:
        """Process a single file: move it to cold storage and record in database."""
        result = {
            "success": False,
            "file_path": str(file_path),
            "error": None,
            "file_record_id": None,
        }

        db = SessionFactory()
        try:
            source_base = Path(path.source_path)

            # Pre-check: verify file still exists
            if not file_path.exists():
                # File disappeared between scan and processing
                logger.debug(f"File no longer exists, skipping: {file_path}")
                result["success"] = True
                result["skipped"] = True
                return result

            # Get file size with retry for transient network errors
            file_size = None
            for attempt in range(3):
                try:
                    if file_path.is_symlink():
                        try:
                            actual_file = file_path.resolve(strict=True)
                            file_size = actual_file.stat().st_size
                        except (OSError, RuntimeError):
                            file_size = file_path.stat().st_size
                    else:
                        file_size = file_path.stat().st_size
                    break  # Success, exit retry loop
                except (OSError, FileNotFoundError) as e:
                    if attempt < 2:
                        # Wait briefly and retry (helps with network mount transient errors)
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    # File genuinely doesn't exist or is inaccessible
                    if not file_path.exists():
                        logger.debug(f"File disappeared during processing: {file_path}")
                        result["success"] = True
                        result["skipped"] = True
                        return result
                    result["error"] = f"Cannot stat source file: {e}"
                    return result

            if file_size is None:
                result["error"] = "Could not determine file size"
                return result

            # Select storage location using routing service
            storage_location = storage_routing_service.select_storage_location(db, path, file_size)

            if not storage_location:
                result["error"] = "No suitable storage location available"
                return result

            dest_base = Path(storage_location.path)
            dest_path = FileMover.preserve_directory_structure(file_path, source_base, dest_base)

            # Lock file inventory record for update
            inventory_entry = (
                db.query(FileInventory)
                .with_for_update()
                .filter(FileInventory.path_id == path.id, FileInventory.file_path == str(file_path))
                .first()
            )

            if not inventory_entry:
                # File not in inventory - might have been deleted or moved by another process
                logger.warning(f"File not found in inventory: {file_path}")
                result["success"] = True
                result["skipped"] = True
                return result

            # Mark file as MIGRATING
            old_status = inventory_entry.status
            inventory_entry.status = FileStatus.MIGRATING
            db.commit()

            try:
                # Get original stats before moving
                original_stat = None
                try:
                    if file_path.is_symlink():
                        try:
                            actual_file = file_path.resolve(strict=True)
                            original_stat = actual_file.stat()
                            file_size = original_stat.st_size
                        except (OSError, RuntimeError):
                            original_stat = file_path.stat()
                            file_size = original_stat.st_size
                    else:
                        original_stat = file_path.stat()
                        file_size = original_stat.st_size
                except (OSError, FileNotFoundError) as e:
                    # Rollback status
                    inventory_entry.status = old_status
                    db.commit()
                    result["error"] = f"Cannot stat source file: {e}"
                    return result

                # Progress tracking
                file_name = file_path.name

                def progress_callback(bytes_transferred: int):
                    scan_progress_manager.update_file_progress(
                        path.id, file_name, bytes_transferred
                    )

                scan_progress_manager.start_file_operation(
                    path.id, file_name, "move_to_cold", file_size
                )

                # Calculate checksum before move
                checksum_before = checksum_verifier.calculate_checksum(file_path)

                # Move file with transaction pattern and checksum verification
                success, error, checksum_after = FileMover.move_with_rollback(
                    file_path, dest_path, path.operation_type, verify_checksum=True
                )

                if success:
                    # Preserve timestamps
                    try:
                        if original_stat and dest_path.exists():
                            os.utime(dest_path, (original_stat.st_atime, original_stat.st_mtime))
                    except OSError as e:
                        logger.warning(f"Could not preserve timestamps for {dest_path}: {e}")

                    # Record in database
                    file_record_id = self._record_file_in_db(
                        db,
                        path,
                        file_path,
                        dest_path,
                        file_size,
                        matched_criteria_ids,
                        storage_location.id,
                    )

                    # Update inventory entry
                    inventory_entry.storage_type = StorageType.COLD
                    inventory_entry.cold_storage_location_id = storage_location.id
                    inventory_entry.status = FileStatus.ACTIVE
                    # For MOVE/SYMLINK, the record logically moves to the cold storage path
                    if path.operation_type in ["move", "symlink"]:
                        inventory_entry.file_path = str(dest_path)
                    db.commit()

                    # Log to audit trail
                    audit_trail_service.log_freeze_operation(
                        db=db,
                        file=inventory_entry,
                        source_path=file_path,
                        dest_path=dest_path,
                        storage_location_id=storage_location.id,
                        checksum_before=checksum_before,
                        checksum_after=checksum_after,
                        success=True,
                        initiated_by="automatic_scan",
                    )

                    result["success"] = True
                    result["file_record_id"] = file_record_id
                    scan_progress_manager.complete_file_operation(
                        path.id, file_name, "move_to_cold", success=True
                    )
                else:
                    # Rollback status on failure
                    inventory_entry.status = old_status
                    db.commit()

                    # Log failed operation to audit trail
                    audit_trail_service.log_freeze_operation(
                        db=db,
                        file=inventory_entry,
                        source_path=file_path,
                        dest_path=dest_path,
                        storage_location_id=storage_location.id,
                        checksum_before=checksum_before,
                        checksum_after=None,
                        success=False,
                        error_message=error,
                        initiated_by="automatic_scan",
                    )

                    result["error"] = f"Failed to move {file_path}: {error}"
                    scan_progress_manager.complete_file_operation(
                        path.id, file_name, "move_to_cold", success=False, error=error
                    )

            except Exception as move_error:
                # Rollback status on exception
                inventory_entry.status = old_status
                db.commit()
                raise move_error

        except Exception as e:
            result["error"] = f"Error processing {file_path}: {e!s}"
            logger.exception(f"Error processing {file_path}")
        finally:
            db.close()

        return result

    def _thaw_single_file(
        self, symlink_path: Path, cold_storage_path: Path, path: MonitoredPath
    ) -> dict:
        """Thaw a single file (move back from cold to hot storage)."""
        result = {
            "success": False,
            "symlink_path": str(symlink_path),
            "cold_storage_path": str(cold_storage_path),
            "error": None,
        }

        db = SessionFactory()
        try:
            # Get file inventory record with lock
            inventory_entry = (
                db.query(FileInventory)
                .with_for_update()
                .filter(
                    FileInventory.path_id == path.id, FileInventory.file_path == str(symlink_path)
                )
                .first()
            )

            if inventory_entry:
                # Mark as MIGRATING
                old_status = inventory_entry.status
                inventory_entry.status = FileStatus.MIGRATING
                db.commit()

                try:
                    if symlink_path.exists() and symlink_path.is_symlink():
                        symlink_path.unlink()

                    try:
                        symlink_path.parent.mkdir(parents=True, exist_ok=True)
                        stat_info = cold_storage_path.stat()

                        # Calculate checksum before move
                        checksum_before = checksum_verifier.calculate_checksum(cold_storage_path)

                        # Move file with verification
                        try:
                            cold_storage_path.rename(symlink_path)
                        except OSError:
                            shutil.copy2(str(cold_storage_path), str(symlink_path))
                            os.utime(
                                str(symlink_path),
                                ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns),
                            )
                            cold_storage_path.unlink()

                        # Verify checksum after move
                        checksum_after = checksum_verifier.calculate_checksum(symlink_path)
                        if checksum_before and checksum_after != checksum_before:
                            logger.error(
                                f"Checksum mismatch after thaw: {checksum_before[:16]}... != {checksum_after[:16]}..."
                            )
                            # Rollback - move file back
                            if cold_storage_path.exists():
                                symlink_path.unlink()
                            inventory_entry.status = old_status
                            db.commit()
                            result["error"] = "Checksum verification failed after thaw"
                            return result

                        file_record = (
                            db.query(FileRecord)
                            .filter(FileRecord.cold_storage_path == str(cold_storage_path))
                            .first()
                        )

                        if file_record:
                            db.delete(file_record)

                        # Update inventory
                        inventory_entry.storage_type = StorageType.HOT
                        inventory_entry.status = FileStatus.ACTIVE
                        inventory_entry.cold_storage_location_id = None
                        db.commit()

                        # Log to audit trail
                        audit_trail_service.log_thaw_operation(
                            db=db,
                            file=inventory_entry,
                            source_path=cold_storage_path,
                            dest_path=symlink_path,
                            checksum_before=checksum_before,
                            checksum_after=checksum_after,
                            success=True,
                            initiated_by="automatic_scan",
                        )

                        result["success"] = True

                    except Exception as e:
                        # Rollback status on failure
                        inventory_entry.status = old_status
                        db.commit()
                        result["error"] = f"Failed to move file back {cold_storage_path}: {e!s}"

                except Exception as move_error:
                    # Rollback status on exception
                    inventory_entry.status = old_status
                    db.commit()
                    raise move_error
            else:
                # No inventory record - proceed without tracking
                try:
                    if symlink_path.exists() and symlink_path.is_symlink():
                        symlink_path.unlink()

                    symlink_path.parent.mkdir(parents=True, exist_ok=True)
                    stat_info = cold_storage_path.stat()

                    try:
                        cold_storage_path.rename(symlink_path)
                    except OSError:
                        shutil.copy2(str(cold_storage_path), str(symlink_path))
                        os.utime(
                            str(symlink_path),
                            ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns),
                        )
                        cold_storage_path.unlink()

                    result["success"] = True

                except Exception as e:
                    result["error"] = f"Failed to move file back {cold_storage_path}: {e!s}"

        except Exception as e:
            result["error"] = f"Error thawing {cold_storage_path}: {e!s}"
        finally:
            db.close()

        return result

    def _record_file_in_db(
        self,
        db: Session,
        path: MonitoredPath,
        file_path: Path,
        dest_path: Path,
        file_size: int,
        matched_criteria_ids: list,
        storage_location_id: int,
    ) -> int:
        """Record a file in the database after moving.

        Transitions the FileInventory record from HOT to COLD if it's a MOVE or SYMLINK.
        """
        existing_record = (
            db.query(FileRecord)
            .filter(
                (FileRecord.original_path == str(file_path))
                | (FileRecord.cold_storage_path == str(dest_path))
            )
            .first()
        )

        if existing_record:
            existing_record.cold_storage_path = str(dest_path)
            existing_record.file_size = file_size
            existing_record.operation_type = path.operation_type
            existing_record.criteria_matched = json.dumps(matched_criteria_ids)
            existing_record.path_id = path.id
            existing_record.cold_storage_location_id = storage_location_id
            db.commit()
            file_record_id = existing_record.id
        else:
            file_record = FileRecord(
                path_id=path.id,
                original_path=str(file_path),
                cold_storage_path=str(dest_path),
                file_size=file_size,
                operation_type=path.operation_type,
                criteria_matched=json.dumps(matched_criteria_ids),
                cold_storage_location_id=storage_location_id,
            )
            db.add(file_record)
            db.commit()
            db.refresh(file_record)
            file_record_id = file_record.id

        # Update inventory record (transition from HOT to COLD or update COLD)
        inventory_entry = (
            db.query(FileInventory)
            .filter(FileInventory.path_id == path.id, FileInventory.file_path == str(file_path))
            .first()
        )

        if inventory_entry:
            # Transition existing record to COLD
            # For MOVE/SYMLINK, the record logically moves to the cold storage path
            if path.operation_type in ["move", "symlink"]:
                inventory_entry.file_path = str(dest_path)
                inventory_entry.storage_type = StorageType.COLD
                inventory_entry.status = FileStatus.ACTIVE
                inventory_entry.cold_storage_location_id = storage_location_id
            else:
                # For COPY, original stays ACTIVE/HOT, and a new record will be created for COLD during next scan
                inventory_entry.status = FileStatus.ACTIVE

            db.commit()
        else:
            # If no inventory record existed, it will be picked up in the sync phase or next scan
            pass

        return file_record_id

    def _recursive_scandir(self, path: Path) -> Iterator[os.DirEntry]:
        """Generator for recursive directory scanning."""
        try:
            with os.scandir(str(path)) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    if any(fnmatch.fnmatch(entry.name, p) for p in self.IGNORED_PATTERNS):
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        yield from self._recursive_scandir(Path(entry.path))
                    else:
                        yield entry
        except (OSError, PermissionError):
            pass

    def _update_file_inventory(
        self,
        path: MonitoredPath,
        db: Session,
        hot_files: Optional[List[Dict]] = None,
        cold_files: Optional[List[Dict]] = None,
        scan_start_time: Optional[datetime] = None,
    ) -> int:
        """Update database inventory for both storage tiers using provided metadata."""
        updated_count = 0
        if scan_start_time is None:
            scan_start_time = datetime.now(tz=timezone.utc)

        # Sync hot tier
        if hot_files is not None:
            updated_count += self._update_db_entries_batch(path, hot_files, StorageType.HOT, db)
        else:
            hot_files_list = self._scan_flat_list(path.source_path)
            updated_count += self._update_db_entries_batch(
                path, hot_files_list, StorageType.HOT, db
            )

        # Sync cold tier
        if cold_files is not None:
            updated_count += self._update_db_entries_batch(path, cold_files, StorageType.COLD, db)
        else:
            cold_files_list = self._scan_flat_list(path.cold_storage_path)
            updated_count += self._update_db_entries_batch(
                path, cold_files_list, StorageType.COLD, db
            )

        # Delete inventory entries for files that are no longer found
        # Use scan_start_time to avoid deleting files that were just scanned
        # We give a 1-minute grace period for clock drift/duration
        cutoff = scan_start_time - timedelta(minutes=1)

        missing_query = db.query(FileInventory).filter(
            FileInventory.path_id == path.id,
            FileInventory.last_seen < cutoff,
            FileInventory.status == FileStatus.ACTIVE,
        )

        # Get the count of records to be deleted before deleting them
        missing_count = missing_query.count()

        if missing_count > 0:
            missing_query.delete(synchronize_session=False)
            db.commit()

        return updated_count + missing_count

    def _scan_flat_list(self, directory_path: str) -> List[Dict]:
        """Get metadata for inventory updates.

        Note: Symlinks are excluded from results to prevent them from appearing
        in the file inventory. Symlinks to cold storage are handled separately
        during the scan phase.
        """
        results = []
        if not os.path.exists(directory_path):
            return results

        for entry in self._recursive_scandir(Path(directory_path)):
            try:
                # Skip symlinks - they should not be added to inventory
                is_symlink = Path(entry.path).is_symlink()
                if is_symlink:
                    continue

                stat = entry.stat(follow_symlinks=False)

                results.append(
                    {
                        "path": entry.path,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        "atime": datetime.fromtimestamp(stat.st_atime, tz=timezone.utc),
                        "ctime": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                    }
                )
            except OSError:
                continue
        return results

    def _update_db_entries_batch(
        self, path: MonitoredPath, files: List[Dict], tier: StorageType, db: Session
    ) -> int:
        """Synchronize file metadata with the database in batches for performance."""
        from app.models import TagRule
        from app.services.file_metadata import FileMetadataExtractor
        from app.services.tag_rule_service import TagRuleService

        count = 0
        tag_rule_service = TagRuleService(db)
        scan_time = datetime.now(tz=timezone.utc)

        # Pre-fetch tag rules to avoid N+1 queries during rules application
        tag_rules = (
            db.query(TagRule)
            .filter(TagRule.enabled)
            .order_by(TagRule.priority.desc(), TagRule.created_at.asc())
            .all()
        )

        # Process in batches to avoid N+1 queries and memory issues
        batch_size = 100
        for i in range(0, len(files), batch_size):
            batch = files[i : i + batch_size]
            batch_paths = [f["path"] for f in batch]

            # Bulk fetch existing entries for this batch
            existing_entries = {
                e.file_path: e
                for e in db.query(FileInventory)
                .filter(FileInventory.path_id == path.id, FileInventory.file_path.in_(batch_paths))
                .all()
            }

            new_files_batch = []
            updated_files_batch = []
            touched_entries = []

            for info in batch:
                file_path_str = info["path"]
                entry = existing_entries.get(file_path_str)

                if entry:
                    # Always update last_seen for files found during scan
                    entry.last_seen = scan_time
                    touched_entries.append(entry)

                    updated = False
                    if (
                        entry.file_size != info["size"]
                        or entry.status != FileStatus.ACTIVE
                        or entry.storage_type != tier
                    ):
                        entry.file_size = info["size"]
                        entry.file_mtime = info["mtime"]
                        entry.file_atime = info["atime"]
                        entry.file_ctime = info["ctime"]
                        entry.status = FileStatus.ACTIVE
                        entry.storage_type = tier
                        updated = True

                    # Extract metadata if missing
                    if entry.file_extension is None or entry.mime_type is None:
                        try:
                            file_path = Path(file_path_str)
                            if file_path.exists():
                                extension, mime_type, checksum = (
                                    FileMetadataExtractor.extract_metadata(file_path)
                                )
                                if entry.file_extension is None and extension:
                                    entry.file_extension = extension
                                    updated = True
                                if entry.mime_type is None and mime_type:
                                    entry.mime_type = mime_type
                                    updated = True
                                if (
                                    entry.checksum is None
                                    and checksum
                                    and info["size"] < 1024 * 1024 * 100
                                ):
                                    entry.checksum = checksum
                                    updated = True
                        except Exception as e:
                            logger.debug(f"Could not extract metadata for {file_path_str}: {e}")

                    if updated:
                        updated_files_batch.append(entry)
                    count += 1
                else:
                    # New file
                    extension = None
                    mime_type = None
                    checksum = None

                    try:
                        file_path = Path(file_path_str)
                        if file_path.exists():
                            extension, mime_type, checksum = FileMetadataExtractor.extract_metadata(
                                file_path
                            )
                    except Exception as e:
                        logger.debug(f"Could not extract metadata for {file_path_str}: {e}")

                    new_entry = FileInventory(
                        path_id=path.id,
                        file_path=file_path_str,
                        storage_type=tier,
                        file_size=info["size"],
                        file_mtime=info["mtime"],
                        file_atime=info["atime"],
                        file_ctime=info["ctime"],
                        status=FileStatus.ACTIVE,
                        file_extension=extension,
                        mime_type=mime_type,
                        checksum=checksum,
                        last_seen=scan_time,
                    )
                    db.add(new_entry)
                    new_files_batch.append(new_entry)
                    count += 1

            # Commit batch
            if touched_entries or new_files_batch:
                db.commit()

                # Apply tag rules
                for file_entry in new_files_batch:
                    try:
                        db.refresh(file_entry)
                        tag_rule_service.apply_rules_to_file(file_entry, rules=tag_rules)
                    except Exception as e:
                        logger.exception(
                            f"Error applying tag rules to new file {file_entry.file_path}: {e}"
                        )

                for file_entry in updated_files_batch:
                    try:
                        tag_rule_service.apply_rules_to_file(file_entry, rules=tag_rules)
                    except Exception as e:
                        logger.exception(
                            f"Error applying tag rules to updated file {file_entry.file_path}: {e}"
                        )

        return count

    def _update_db_entries(
        self, path: MonitoredPath, files: List[Dict], tier: StorageType, db: Session
    ) -> int:
        """Deprecated: Use _update_db_entries_batch instead."""
        return self._update_db_entries_batch(path, files, tier, db)


# Singleton instance
file_workflow_service = FileWorkflowService()
