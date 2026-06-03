"""Unified file workflow service - scanning, moving, and inventory management."""

import fnmatch
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Dict, Iterator, List, Optional, Set

from sqlalchemy import or_
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine
from app.models import (
    ColdStorageLocation,
    CriterionType,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    PinnedFile,
    ScanStatus,
    StorageType,
)
from app.services.audit_trail_service import (
    audit_trail_service,  # Backward-compatible test patch target
)
from app.services.checksum_verifier import (
    checksum_verifier,  # Backward-compatible test patch target
)
from app.services.cold_storage_backends import get_backend
from app.services.criteria_matcher import CriteriaMatcher
from app.services.file_cleanup import FileCleanup
from app.services.file_freezer import FileFreezer
from app.services.file_mover import FileMover  # Backward-compatible test patch target
from app.services.file_reconciliation import FileReconciliation
from app.services.file_thawer import FileThawer
from app.services.scan_progress import scan_progress_manager
from app.services.storage_routing_service import storage_routing_service
from app.utils.network_detection import check_atime_availability

logger = logging.getLogger(__name__)

# Thread-local session factory for concurrent database access
SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MAX_CONCURRENT_MIGRATIONS_CAP = 10


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
        path_id = path.id
        scan_id, scan_started = scan_progress_manager.start_scan(path_id, total_files=0)

        if not scan_started:
            logger.warning(f"Scan already running for path {path_id}, skipping")
            return {
                "path_id": path_id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "errors": [],
                "scan_skipped": True,
                "scan_skipped_reason": "A scan is already running for this path",
            }

        logger.info(f"Started scan {scan_id} for path {path_id}")

        # Mark scan as pending
        path.last_scan_status = ScanStatus.PENDING
        db.commit()

        try:
            if path.error_message:
                logger.warning(
                    f"Path {path.name} (ID: {path.id}) is in error state: {path.error_message}"
                )
                scan_progress_manager.finish_scan(path_id, status="failed")
                # Update scan status in database
                error_log = f"Path is in error state: {path.error_message}"
                path.last_scan_at = datetime.now(tz=timezone.utc)
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = error_log
                db.commit()
                return {
                    "path_id": path_id,
                    "files_found": 0,
                    "files_moved": 0,
                    "files_cleaned": 0,
                    "errors": [error_log],
                }

            results = {
                "path_id": path_id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "files_skipped": 0,
                "total_scanned": 0,
                "errors": [],
            }

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
                scan_progress_manager.update_total_files(path_id, total_files_to_process)

                # Process thawing
                if files_to_thaw:
                    logger.info(f"Processing {len(files_to_thaw)} files to thaw")
                    validated_max = (
                        path.max_concurrent_migrations
                        if path.max_concurrent_migrations and path.max_concurrent_migrations > 0
                        else 1
                    )
                    max_workers = max(
                        1, min(validated_max, len(files_to_thaw), MAX_CONCURRENT_MIGRATIONS_CAP)
                    )
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

                            # Check for stop request after each completed thaw
                            if scan_progress_manager.is_stop_requested(path_id):
                                logger.info(
                                    "Stop requested during thaw phase for path %s,"
                                    " cancelling remaining operations",
                                    path_id,
                                )
                                for f in future_to_thaw:
                                    f.cancel()
                                break

                # Bail out early if stop was requested during thaw phase
                if scan_progress_manager.is_stop_requested(path_id):
                    scan_progress_manager.finish_scan(path_id, status="stopped")
                    path.last_scan_at = datetime.now(tz=timezone.utc)
                    path.last_scan_status = ScanStatus.STOPPED
                    db.commit()
                    return results

                # Process moves to cold storage
                if matching_files:
                    logger.info(f"Processing {len(matching_files)} files to cold storage")
                    validated_max = (
                        path.max_concurrent_migrations
                        if path.max_concurrent_migrations and path.max_concurrent_migrations > 0
                        else 1
                    )
                    max_workers = max(
                        1, min(validated_max, len(matching_files), MAX_CONCURRENT_MIGRATIONS_CAP)
                    )
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

                            # Check for stop request after each completed freeze
                            if scan_progress_manager.is_stop_requested(path_id):
                                logger.info(
                                    "Stop requested during freeze phase for path %s,"
                                    " cancelling remaining operations",
                                    path_id,
                                )
                                for f in future_to_file:
                                    f.cancel()
                                break

                # Bail out early if stop was requested during freeze phase
                if scan_progress_manager.is_stop_requested(path_id):
                    scan_progress_manager.finish_scan(path_id, status="stopped")
                    path.last_scan_at = datetime.now(tz=timezone.utc)
                    path.last_scan_status = ScanStatus.STOPPED
                    db.commit()
                    return results

                # Cleanup phase
                try:
                    cleanup_results = FileCleanup.cleanup_missing_files(db, path_id=path_id)
                    results["files_cleaned"] = cleanup_results["removed"]
                    if cleanup_results["errors"]:
                        results["errors"].extend(cleanup_results["errors"])

                    duplicate_results = FileCleanup.cleanup_duplicates(db, path_id=path_id)
                    results["files_cleaned"] += duplicate_results["removed"]
                    if duplicate_results["errors"]:
                        results["errors"].extend(duplicate_results["errors"])

                    # Clean up symlink entries from inventory
                    symlink_results = FileCleanup.cleanup_symlink_inventory_entries(db, path_id=path_id)
                    results["files_cleaned"] += symlink_results["removed"]
                    if symlink_results["errors"]:
                        results["errors"].extend(symlink_results["errors"])
                except Exception as e:
                    logger.warning(f"Error during cleanup for path {path_id}: {e!s}")

                # Reconciliation phase
                try:
                    reconciliation_path = (
                        db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first() or path
                    )
                    reconciliation_stats = FileReconciliation.reconcile_missing_symlinks(
                        reconciliation_path, db
                    )
                    if reconciliation_stats["symlinks_created"] > 0:
                        logger.info(
                            f"Created {reconciliation_stats['symlinks_created']} missing symlinks"
                        )
                    if reconciliation_stats["errors"]:
                        results["errors"].extend(reconciliation_stats["errors"])
                except Exception as e:
                    results["errors"].append(f"Reconciliation error: {e!s}")

            except Exception as e:
                results["errors"].append(f"Error processing path {path_id}: {e!s}")
                scan_progress_manager.finish_scan(path_id, status="failed")
                # Update scan status in database
                path.last_scan_at = datetime.now(tz=timezone.utc)
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = "\n".join(results["errors"])
                db.commit()
                return results

            scan_progress_manager.finish_scan(path_id, status="completed")
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
                f"Unexpected error in process_path for path {path_id}: {e!s}", exc_info=True
            )
            scan_progress_manager.finish_scan(path_id, status="failed")
            # Update scan status in database
            error_log = f"Unexpected error: {e!s}"
            try:
                path.last_scan_at = datetime.now(tz=timezone.utc)
                path.last_scan_status = ScanStatus.FAILURE
                path.last_scan_error_log = error_log
                db.commit()
            except Exception:
                # If we can't update the database, log and continue
                logger.warning(f"Could not update scan status for path {path_id}")
            return {
                "path_id": path_id,
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
            local_symlink_locations = []
            for location in path.storage_locations:
                try:
                    backend = get_backend(location)
                except Exception:
                    continue
                if (
                    backend.backend_name() == "local"
                    and location.operation_mode == OperationType.SYMLINK
                ):
                    local_symlink_locations.append(location)

            for location in local_symlink_locations:
                atime_available, error_msg = check_atime_availability(location.path)
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
        pinned_path_strings = {str(p.file_path) for p in pinned}

        # Cold storage roots for symlink detection
        cold_roots = []
        for location in path.storage_locations:
            try:
                backend = get_backend(location)
            except Exception:
                continue
            if backend.backend_name() == "local":
                cold_roots.append(Path(location.path))

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
                    # Check if the resolved path is in ANY of our cold storage roots
                    for root in cold_roots:
                        try:
                            resolved.relative_to(root)
                            is_symlink_to_cold = True
                            break
                        except ValueError:
                            continue
                except (OSError, RuntimeError):
                    continue

            # Backward-compatibility path-mode behavior for orphaned symlinks.
            if is_symlink_to_cold and path.operation_type == OperationType.MOVE:
                try:
                    file_path.unlink()
                    logger.info(
                        f"Removed orphaned symlink {file_path} "
                        f"(operation_type changed from symlink to move)"
                    )
                except OSError as e:
                    logger.warning(f"Could not remove orphaned symlink {file_path}: {e}")
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
                elif path.operation_type == OperationType.COPY and actual_file_path:
                    # COPY requires a real file in hot storage, not a symlink.
                    files_to_thaw.append((file_path, actual_file_path))
                else:
                    files_skipped_cold += 1
            except (OSError, PermissionError) as e:
                logger.debug(f"Access error for {file_path}: {e}")
                continue

        # Scan local cold storage locations for metadata sync.
        for location in path.storage_locations:
            try:
                backend = get_backend(location)
            except Exception:
                continue
            if backend.backend_name() != "local":
                continue
            location_base = Path(location.path)
            if not location_base.exists() or not location_base.is_dir():
                continue
            for entry in self._recursive_scandir(location_base):
                file_count += 1
                try:
                    stat_info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue

                if not entry.is_symlink():
                    cold_files_metadata.append(
                        {
                            "path": entry.path,
                            "size": stat_info.st_size,
                            "mtime": datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc),
                            "atime": datetime.fromtimestamp(stat_info.st_atime, tz=timezone.utc),
                            "ctime": datetime.fromtimestamp(stat_info.st_ctime, tz=timezone.utc),
                            "location_id": location.id,
                        }
                    )

        # Evaluate thaw candidates from tracked cold inventory/records. This supports mixed
        # local + remote backends because the decision is based on backend-specific access.
        thaw_candidates, thaw_skipped = self._collect_cold_thaw_candidates(
            path=path,
            db=db,
            pinned_paths=pinned_paths,
            pinned_path_strings=pinned_path_strings,
        )
        files_to_thaw.extend(thaw_candidates)
        files_skipped_cold += thaw_skipped

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
            "to_hot": list(dict.fromkeys(files_to_thaw)),
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
        operation_id = None
        path_id = path.id
        try:
            db_path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
            if not db_path:
                result["error"] = f"Path not found: {path_id}"
                return result

            if not file_path.exists():
                logger.debug(f"File no longer exists, skipping: {file_path}")
                result["success"] = True
                result["skipped"] = True
                return result

            file_size = None
            for attempt in range(3):
                try:
                    file_size = file_path.stat().st_size
                    break
                except (OSError, FileNotFoundError) as e:
                    if attempt < 2:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    if not file_path.exists():
                        result["success"] = True
                        result["skipped"] = True
                        return result
                    result["error"] = f"Cannot stat source file: {e}"
                    return result
            if file_size is None:
                result["error"] = "Could not determine file size"
                return result

            storage_location = storage_routing_service.select_storage_location(
                db, db_path, file_size
            )
            if not storage_location:
                result["error"] = "No suitable storage location available"
                return result

            operation_mode = self._resolve_operation_mode(storage_location, db_path)
            operation_id = scan_progress_manager.start_file_operation(
                path_id,
                file_path.name,
                "move_to_cold",
                file_size,
                file_path=str(file_path),
                destination_path=storage_location.path,
            )

            inventory_entry = (
                db.query(FileInventory)
                .with_for_update()
                .filter(
                    FileInventory.path_id == db_path.id, FileInventory.file_path == str(file_path)
                )
                .first()
            )
            if not inventory_entry:
                result["success"] = True
                result["skipped"] = True
                return result

            backend = None
            try:
                backend = get_backend(storage_location)
            except Exception:
                # Only silently fall back to the legacy local-path mover for LOCAL backends.
                # For remote backends (S3, GDrive) a backend resolution failure is a real error.
                if storage_location.backend_type and storage_location.backend_type.value != "local":
                    raise
                backend = None

            if backend is None:
                source_base = Path(db_path.source_path)
                dest_base = Path(storage_location.path)
                dest_path = FileMover.preserve_directory_structure(
                    file_path, source_base, dest_base
                )
                checksum_before = checksum_verifier.calculate_checksum(file_path)

                def progress_callback(bytes_transferred: int):
                    scan_progress_manager.update_file_progress(
                        path_id, operation_id, bytes_transferred
                    )

                success, error, checksum_after = FileMover.move_with_rollback(
                    file_path,
                    dest_path,
                    db_path.operation_type,
                    verify_checksum=True,
                    progress_callback=progress_callback,
                )
                if not success:
                    result["error"] = f"Failed to move {file_path}: {error}"
                    scan_progress_manager.complete_file_operation(
                        path_id,
                        operation_id,
                        "move_to_cold",
                        success=False,
                        error=result["error"],
                    )
                    return result

                file_record_id = self._record_file_in_db(
                    db,
                    db_path,
                    file_path,
                    dest_path,
                    file_size,
                    matched_criteria_ids,
                    storage_location.id,
                )
                result["file_record_id"] = file_record_id
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
                scan_progress_manager.complete_file_operation(
                    path_id, operation_id, "move_to_cold", success=True
                )
                return result

            success, error, _cold_storage_path = FileFreezer.freeze_file(
                file=inventory_entry,
                monitored_path=db_path,
                storage_location=storage_location,
                pin=False,
                db=db,
                initiated_by="automatic_scan",
            )
            if not success:
                result["error"] = f"Failed to move {file_path}: {error}"
                scan_progress_manager.complete_file_operation(
                    path_id,
                    operation_id,
                    "move_to_cold",
                    success=False,
                    error=result["error"],
                )
                return result

            latest_record = (
                db.query(FileRecord)
                .filter(
                    FileRecord.path_id == db_path.id,
                    FileRecord.original_path == str(file_path),
                    FileRecord.cold_storage_location_id == storage_location.id,
                )
                .order_by(FileRecord.moved_at.desc())
                .first()
            )
            if latest_record:
                latest_record.criteria_matched = json.dumps(matched_criteria_ids)
                latest_record.operation_type = operation_mode
                result["file_record_id"] = latest_record.id
                db.commit()

            # Preserve historical workflow behavior: COPY keeps hot inventory entry.
            if operation_mode == OperationType.COPY:
                refreshed = (
                    db.query(FileInventory).filter(FileInventory.id == inventory_entry.id).first()
                )
                if refreshed:
                    refreshed.storage_type = StorageType.HOT
                    refreshed.status = FileStatus.ACTIVE
                    refreshed.cold_storage_location_id = None
                    refreshed.file_path = str(file_path)
                    db.commit()

            result["success"] = True
            scan_progress_manager.complete_file_operation(
                path_id, operation_id, "move_to_cold", success=True
            )
            return result
        except Exception as e:
            result["error"] = f"Error processing {file_path}: {e!s}"
            logger.exception(f"Error processing {file_path}")
            if operation_id is not None:
                scan_progress_manager.complete_file_operation(
                    path_id, operation_id, "move_to_cold", success=False, error=result["error"]
                )
            return result
        finally:
            db.close()

    def _thaw_single_file(self, symlink_path, cold_storage_path, path: MonitoredPath) -> dict:
        """Thaw a single file (move back from cold to hot storage)."""
        result = {
            "success": False,
            "symlink_path": str(symlink_path),
            "cold_storage_path": str(cold_storage_path),
            "error": None,
        }

        db = SessionFactory()
        operation_id = None
        path_id = path.id
        try:
            hot_path = Path(str(symlink_path))
            cold_reference = str(cold_storage_path)
            inventory_entry = (
                db.query(FileInventory)
                .with_for_update()
                .filter(
                    FileInventory.path_id == path_id,
                    FileInventory.storage_type == StorageType.COLD,
                    FileInventory.file_path.in_([cold_reference, str(hot_path)]),
                )
                .first()
            )
            cold_path_for_size = Path(cold_reference)
            if cold_path_for_size.exists():
                try:
                    file_size = cold_path_for_size.stat().st_size
                except OSError:
                    file_size = inventory_entry.file_size if inventory_entry else 0
            else:
                file_size = inventory_entry.file_size if inventory_entry else 0

            operation_id = scan_progress_manager.start_file_operation(
                path_id,
                hot_path.name,
                "move_to_hot",
                file_size,
                file_path=cold_reference,
                destination_path=str(hot_path),
            )

            def progress_callback(bytes_transferred: int):
                scan_progress_manager.update_file_progress(path_id, operation_id, bytes_transferred)

            file_record = (
                db.query(FileRecord)
                .filter(
                    FileRecord.path_id == path_id,
                    (
                        (FileRecord.cold_storage_path == cold_reference)
                        | (FileRecord.original_path == str(hot_path))
                    ),
                )
                .order_by(FileRecord.moved_at.desc())
                .first()
            )
            if not file_record:
                local_cold_path = Path(cold_reference)
                if not local_cold_path.exists():
                    result["success"] = True
                    result["skipped"] = True
                    scan_progress_manager.complete_file_operation(
                        path_id, operation_id, "move_to_hot", success=True
                    )
                    return result

                try:
                    if hot_path.exists() and hot_path.is_symlink():
                        hot_path.unlink()
                    hot_path.parent.mkdir(parents=True, exist_ok=True)

                    success, error, checksum = FileMover.move_with_rollback(
                        source=local_cold_path,
                        destination=hot_path,
                        operation_type=OperationType.MOVE,
                        verify_checksum=True,
                        progress_callback=progress_callback,
                    )
                    if not success:
                        raise RuntimeError(error or "Thaw failed during file transfer")

                    checksum_before = checksum
                    checksum_after = checksum

                    if inventory_entry:
                        inventory_entry.file_path = str(hot_path)
                        inventory_entry.storage_type = StorageType.HOT
                        inventory_entry.status = FileStatus.ACTIVE
                        inventory_entry.cold_storage_location_id = None
                        db.commit()
                        audit_trail_service.log_thaw_operation(
                            db=db,
                            file=inventory_entry,
                            source_path=local_cold_path,
                            dest_path=hot_path,
                            checksum_before=checksum_before,
                            checksum_after=checksum_after,
                            success=True,
                            initiated_by="automatic_scan",
                        )

                    result["success"] = True
                    scan_progress_manager.update_file_progress(path_id, operation_id, file_size)
                    scan_progress_manager.complete_file_operation(
                        path_id, operation_id, "move_to_hot", success=True
                    )
                    return result
                except Exception as local_thaw_error:
                    result["error"] = (
                        f"Failed to move file back {cold_reference}: {local_thaw_error!s}"
                    )
                    scan_progress_manager.complete_file_operation(
                        path_id, operation_id, "move_to_hot", success=False, error=result["error"]
                    )
                    return result

            old_status = None
            if inventory_entry:
                old_status = inventory_entry.status
                inventory_entry.status = FileStatus.MIGRATING
                db.commit()

            success, error = FileThawer.thaw_file(
                file_record=file_record,
                pin=False,
                db=db,
                initiated_by="automatic_scan",
                progress_callback=progress_callback,
            )
            if not success:
                if inventory_entry and old_status is not None:
                    inventory_entry.status = old_status
                    db.commit()
                result["error"] = error or f"Failed to move file back {cold_reference}"
                scan_progress_manager.complete_file_operation(
                    path_id, operation_id, "move_to_hot", success=False, error=result["error"]
                )
                return result

            result["success"] = True
            scan_progress_manager.update_file_progress(path_id, operation_id, file_size)
            scan_progress_manager.complete_file_operation(
                path_id, operation_id, "move_to_hot", success=True
            )
            return result
        except Exception as e:
            result["error"] = f"Error thawing {cold_storage_path}: {e!s}"
            if operation_id is not None:
                scan_progress_manager.complete_file_operation(
                    path_id, operation_id, "move_to_hot", success=False, error=result["error"]
                )
            return result
        finally:
            db.close()

    @staticmethod
    def _resolve_operation_mode(
        storage_location: ColdStorageLocation, monitored_path: MonitoredPath
    ) -> OperationType:
        operation_mode = storage_location.operation_mode or monitored_path.operation_type
        if operation_mode == OperationType.MOVE and monitored_path.operation_type in (
            OperationType.COPY,
            OperationType.SYMLINK,
        ):
            return monitored_path.operation_type
        return operation_mode

    def _collect_cold_thaw_candidates(
        self,
        path: MonitoredPath,
        db: Session,
        pinned_paths: set[Path],
        pinned_path_strings: set[str],
    ) -> tuple[list[tuple[Path, str | Path]], int]:
        """Find cold files that now match criteria and should be thawed."""
        candidates: list[tuple[Path, str | Path]] = []
        skipped = 0

        file_records = db.query(FileRecord).filter(FileRecord.path_id == path.id).all()
        if not file_records:
            return candidates, skipped

        records_by_cold = {}
        records_by_original = {}
        for record in file_records:
            if record.cold_storage_path in records_by_cold:
                logger.warning(
                    f"Duplicate FileRecord found for cold_storage_path: {record.cold_storage_path}"
                )
            records_by_cold[record.cold_storage_path] = record

            if record.original_path in records_by_original:
                logger.warning(
                    f"Duplicate FileRecord found for original_path: {record.original_path}"
                )
            records_by_original[record.original_path] = record
        location_ids = {
            record.cold_storage_location_id
            for record in file_records
            if record.cold_storage_location_id is not None
        }
        location_map = {
            location.id: location
            for location in db.query(ColdStorageLocation)
            .filter(ColdStorageLocation.id.in_(location_ids))
            .all()
        }

        cold_inventory_entries = (
            db.query(FileInventory)
            .filter(
                FileInventory.path_id == path.id,
                FileInventory.storage_type == StorageType.COLD,
                FileInventory.status == FileStatus.ACTIVE,
            )
            .all()
        )

        for entry in cold_inventory_entries:
            file_record = records_by_cold.get(entry.file_path) or records_by_original.get(
                entry.file_path
            )
            if not file_record:
                skipped += 1
                continue

            hot_path = Path(file_record.original_path)
            cold_reference = file_record.cold_storage_path

            if (
                hot_path in pinned_paths
                or Path(cold_reference) in pinned_paths
                or str(hot_path) in pinned_path_strings
                or cold_reference in pinned_path_strings
            ):
                skipped += 1
                continue

            location = location_map.get(file_record.cold_storage_location_id)
            if location is None:
                skipped += 1
                continue

            is_active = False
            try:
                backend = get_backend(location)
                if backend.backend_name() == "local" and Path(cold_reference).exists():
                    is_active, _ = CriteriaMatcher.match_file(
                        hot_path, path.criteria, Path(cold_reference)
                    )
                else:
                    is_active, _ = self._match_inventory_criteria(
                        hot_path=hot_path,
                        inventory_entry=entry,
                        criteria=path.criteria,
                    )
            except Exception:
                logger.exception("Error evaluating thaw criteria for %s", cold_reference)
                skipped += 1
                continue

            if is_active:
                cold_value: str | Path = (
                    Path(cold_reference) if backend.backend_name() == "local" else cold_reference
                )
                candidates.append((hot_path, cold_value))
            else:
                skipped += 1

        return candidates, skipped

    def _match_inventory_criteria(
        self, hot_path: Path, inventory_entry: FileInventory, criteria: list
    ) -> tuple[bool, list[int]]:
        """Evaluate criteria from stored metadata when backend path isn't directly stat-able."""
        enabled_criteria = [criterion for criterion in criteria if criterion.enabled]
        if not enabled_criteria:
            return True, []

        matched_ids: list[int] = []
        for criterion in enabled_criteria:
            operator = criterion.operator
            value = criterion.value
            criterion_type = criterion.criterion_type

            if criterion_type == CriterionType.MTIME:
                dt = inventory_entry.file_mtime
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamp = dt.timestamp() if dt else None
                matches = (
                    CriteriaMatcher._match_time(timestamp, operator, value, "mtime")
                    if timestamp is not None
                    else False
                )
            elif criterion_type == CriterionType.ATIME:
                dt = inventory_entry.file_atime
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamp = dt.timestamp() if dt else None
                matches = (
                    CriteriaMatcher._match_time(timestamp, operator, value, "atime")
                    if timestamp is not None
                    else False
                )
            elif criterion_type == CriterionType.CTIME:
                dt = inventory_entry.file_ctime
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamp = dt.timestamp() if dt else None
                matches = (
                    CriteriaMatcher._match_time(timestamp, operator, value, "ctime")
                    if timestamp is not None
                    else False
                )
            elif criterion_type == CriterionType.SIZE:
                matches = CriteriaMatcher._match_size(inventory_entry.file_size, operator, value)
            elif criterion_type == CriterionType.NAME:
                matches = CriteriaMatcher._match_name(
                    hot_path.name, operator, value, case_sensitive=True
                )
            elif criterion_type == CriterionType.INAME:
                matches = CriteriaMatcher._match_name(
                    hot_path.name, operator, value, case_sensitive=False
                )
            elif criterion_type == CriterionType.TYPE:
                matches = value in {"f", "file"}
            else:
                # Conservative behavior: unsupported criteria cannot prove "hot" eligibility.
                matches = False

            if not matches:
                return False, []
            matched_ids.append(criterion.id)

        return True, matched_ids

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
            .with_for_update()
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
            .with_for_update()
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

    @staticmethod
    def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
        """Parse an ISO 8601 string (e.g. from GDrive API) to a timezone-aware datetime."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    def _sync_remote_cold_inventory(
        self,
        path: MonitoredPath,
        db: Session,
        scan_time: datetime,
    ) -> int:
        """Sync FileInventory with remote cold storage backends (e.g. GDrive).

        For each non-local cold storage location that supports listing managed files,
        this method pages through all remote files and:
        - updates last_seen + status for entries already in inventory, and
        - creates new FileInventory (and FileRecord) entries for files found in the
          remote backend that are missing from the local inventory.
        """
        synced = 0
        for location in path.storage_locations:
            try:
                backend = get_backend(location)
            except Exception:
                continue
            if backend.backend_name() == "local":
                continue
            if not hasattr(backend, "list_managed_files"):
                continue
            try:
                synced += self._sync_single_remote_location(path, db, location, backend, scan_time)
            except Exception as e:
                logger.warning(
                    "Remote cold inventory sync failed for location '%s' (id=%s): %s",
                    location.name,
                    location.id,
                    e,
                )
        return synced

    def _sync_single_remote_location(
        self,
        path: MonitoredPath,
        db: Session,
        location: ColdStorageLocation,
        backend,
        scan_time: datetime,
    ) -> int:
        """Sync inventory for one remote cold storage location (one paginated listing).

        Uses ``list_all_folder_files`` when available so that files dropped directly
        into the remote folder (outside the application) are also picked up.  Falls
        back to ``list_managed_files`` for backends that only expose app-managed files.

        Per-file handling:
        - Managed by this location  → refresh last_seen / re-create missing entry.
        - Managed by another location → skip (that location's scan owns it).
        - External (no appProperties) → create a new inventory entry so the file
          becomes visible in the UI.
        """
        # Prefer the broader listing so external files are discovered.
        list_fn = getattr(backend, "list_all_folder_files", None) or getattr(
            backend, "list_managed_files", None
        )
        if list_fn is None:
            return 0

        # Index existing inventory entries for this remote location by storage reference.
        # Include entries where cold_storage_location_id is NULL (legacy entries).
        existing_by_ref: Dict[str, FileInventory] = {
            entry.file_path: entry
            for entry in db.query(FileInventory)
            .filter(
                FileInventory.path_id == path.id,
                FileInventory.storage_type == StorageType.COLD,
                or_(
                    FileInventory.cold_storage_location_id == location.id,
                    FileInventory.cold_storage_location_id.is_(None),
                ),
            )
            .all()
        }

        synced = 0
        page_token: Optional[str] = None

        while True:
            result = list_fn(location, page_size=1000, page_token=page_token)

            for remote_file in result.get("files", []):
                file_id = remote_file.get("id")
                if not file_id:
                    continue

                # Skip files that are managed by a *different* File Fridge location so
                # we don't create duplicate inventory entries across locations.
                ff_location_id = remote_file.get("ff_location_id")
                if ff_location_id and ff_location_id != str(location.id):
                    continue

                is_managed = remote_file.get("is_managed", True)

                storage_reference = backend.build_reference(location, Path(file_id))

                # Check for ID-based filename encryption (self-healing / resilient naming)
                db_id_match = None
                raw_filename = remote_file.get("name", "")
                if raw_filename.startswith("ffenc_") and raw_filename.endswith(".ffenc"):
                    try:
                        db_id_match = int(raw_filename.removeprefix("ffenc_").removesuffix(".ffenc"))
                    except ValueError:
                        db_id_match = None

                matched_entry = None
                if db_id_match is not None:
                    matched_entry = (
                        db.query(FileInventory)
                        .filter(FileInventory.id == db_id_match, FileInventory.path_id == path.id)
                        .first()
                    )

                if matched_entry:
                    is_managed = True
                    is_encrypted = matched_entry.is_encrypted
                    old_storage_ref = matched_entry.file_path
                    
                    # Self-healing: if file_id changed in GDrive, update DB reference
                    if old_storage_ref != storage_reference:
                        logger.info(
                            "Self-healing: file ID %s moved/re-uploaded (new ref=%s, old ref=%s)",
                            db_id_match,
                            storage_reference,
                            old_storage_ref,
                        )
                        matched_entry.file_path = storage_reference
                        
                        # Update the reference in our local cache dictionary
                        if old_storage_ref in existing_by_ref:
                            del existing_by_ref[old_storage_ref]
                        existing_by_ref[storage_reference] = matched_entry
                        
                        # Check and update FileRecord
                        existing_record = (
                            db.query(FileRecord)
                            .filter(
                                FileRecord.path_id == path.id,
                                FileRecord.cold_storage_path == old_storage_ref,
                            )
                            .first()
                        )
                        if existing_record:
                            existing_record.cold_storage_path = storage_reference
                    else:
                        existing_record = (
                            db.query(FileRecord)
                            .filter(
                                FileRecord.path_id == path.id,
                                FileRecord.cold_storage_path == storage_reference,
                            )
                            .first()
                        )

                    # Recover relative path from database FileRecord
                    if existing_record:
                        try:
                            rel = Path(existing_record.original_path).relative_to(Path(path.source_path))
                            relative_path_str = rel.as_posix()
                        except ValueError:
                            relative_path_str = Path(existing_record.original_path).name
                    else:
                        relative_path_str = remote_file.get("relative_path") or raw_filename
                else:
                    # Regular flow for legacy or externally-added files
                    relative_path_str = remote_file.get("relative_path") or remote_file.get("name", "")
                    is_encrypted = relative_path_str.endswith(".ffenc")
                bare_relative = (
                    relative_path_str.removesuffix(".ffenc") if is_encrypted else relative_path_str
                )
                ext = Path(bare_relative).suffix.lower() or None

                # Provide scan_time as fallback for non-nullable file_mtime in DB.
                mtime = self._parse_iso_datetime(remote_file.get("modified_time")) or scan_time
                ctime = self._parse_iso_datetime(remote_file.get("created_time"))

                entry = existing_by_ref.get(storage_reference)
                if entry:
                    # Keep existing entry fresh so it is never treated as stale.
                    entry.last_seen = scan_time
                    if entry.status != FileStatus.ACTIVE:
                        entry.status = FileStatus.ACTIVE
                    # Backfill cold_storage_location_id if it was never set.
                    if entry.cold_storage_location_id is None:
                        entry.cold_storage_location_id = location.id
                else:
                    action = "App-managed" if is_managed else "External"
                    logger.info(
                        "%s file discovered in remote inventory: path_id=%s ref=%s",
                        action,
                        path.id,
                        storage_reference,
                    )
                    entry = FileInventory(
                        path_id=path.id,
                        file_path=storage_reference,
                        storage_type=StorageType.COLD,
                        file_size=remote_file.get("size", 0),
                        file_mtime=mtime,
                        file_atime=None,
                        file_ctime=ctime,
                        status=FileStatus.ACTIVE,
                        file_extension=ext,
                        mime_type=remote_file.get("mime_type"),
                        cold_storage_location_id=location.id,
                        is_encrypted=is_encrypted,
                        last_seen=scan_time,
                    )
                    db.add(entry)
                    existing_by_ref[storage_reference] = entry

                synced += 1

                # Ensure a FileRecord exists so that display_file_path resolves to the
                # original filename rather than the raw storage reference.
                # For app-managed files use the full source-path reconstruction;
                # for external files use just the filename as the display name.
                if bare_relative:
                    if is_managed:
                        original_path = str(Path(path.source_path) / bare_relative)
                    else:
                        original_path = Path(bare_relative).name
                    existing_record = (
                        db.query(FileRecord)
                        .filter(
                            FileRecord.path_id == path.id,
                            FileRecord.cold_storage_path == storage_reference,
                        )
                        .first()
                    )
                    if not existing_record:
                        file_record = FileRecord(
                            path_id=path.id,
                            original_path=original_path,
                            cold_storage_path=storage_reference,
                            cold_storage_location_id=location.id,
                            file_size=remote_file.get("size", 0),
                            operation_type=OperationType.MOVE,
                        )
                        db.add(file_record)
                    elif existing_record.cold_storage_location_id is None:
                        existing_record.cold_storage_location_id = location.id

            page_token = result.get("next_page_token")
            if not page_token:
                break

        db.commit()
        return synced

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

        # Sync cold tier (local backends)
        if cold_files is not None:
            updated_count += self._update_db_entries_batch(path, cold_files, StorageType.COLD, db)
        else:
            cold_files_list = []
            for location in path.storage_locations:
                try:
                    backend = get_backend(location)
                except Exception:
                    continue
                if backend.backend_name() != "local":
                    continue
                for item in self._scan_flat_list(location.path):
                    item["location_id"] = location.id
                    cold_files_list.append(item)
            updated_count += self._update_db_entries_batch(
                path, cold_files_list, StorageType.COLD, db
            )

        # Sync remote cold storage locations (GDrive, S3, etc.) — update last_seen for
        # all files found in the remote backend and re-create any missing inventory entries.
        updated_count += self._sync_remote_cold_inventory(path, db, scan_start_time)

        # Delete inventory entries for files that are no longer found
        # Use scan_start_time to avoid deleting files that were just scanned
        # We give a 1-minute grace period for clock drift/duration
        cutoff = scan_start_time - timedelta(minutes=1)

        missing_query = db.query(FileInventory).filter(
            FileInventory.path_id == path.id,
            FileInventory.last_seen < cutoff,
            FileInventory.status == FileStatus.ACTIVE,
            FileInventory.storage_type == StorageType.HOT,
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

                location_id = info.get("location_id") if tier == StorageType.COLD else None

                if not entry and tier == StorageType.COLD:
                    # Self-healing check for local/file-based backends
                    leaf_name = Path(file_path_str).name
                    if leaf_name.startswith("ffenc_") and leaf_name.endswith(".ffenc"):
                        try:
                            db_id = int(leaf_name.removeprefix("ffenc_").removesuffix(".ffenc"))
                            entry = db.query(FileInventory).filter(FileInventory.id == db_id, FileInventory.path_id == path.id).first()
                            if entry:
                                old_ref = entry.file_path
                                logger.info(
                                    "Self-healing (Local/S3): file ID %s moved (new path=%s, old path=%s)",
                                    db_id,
                                    file_path_str,
                                    old_ref,
                                )
                                entry.file_path = file_path_str
                                
                                # Update FileRecord
                                existing_record = (
                                    db.query(FileRecord)
                                    .filter(
                                        FileRecord.path_id == path.id,
                                        FileRecord.cold_storage_path == old_ref,
                                    )
                                    .first()
                                )
                                if existing_record:
                                    existing_record.cold_storage_path = file_path_str
                        except ValueError:
                            pass

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

                    # Backfill cold_storage_location_id if it was never set.
                    if location_id and entry.cold_storage_location_id is None:
                        entry.cold_storage_location_id = location_id
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
                        cold_storage_location_id=location_id,
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
