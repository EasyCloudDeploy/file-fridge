"""Processes scans and moves files."""
import logging
import json
import os
import shutil
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import MonitoredPath, FileRecord, FileInventory, FileStatus, OperationType
from app.services.file_scanner import FileScanner
from app.services.file_mover import FileMover
from app.services.file_cleanup import FileCleanup
from app.services.file_reconciliation import FileReconciliation
from app.services.scan_progress import scan_progress_manager
from app.database import engine

logger = logging.getLogger(__name__)

# Create a thread-local session factory for concurrent database access
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class ScanProcessor:
    """Processes file scans and handles file movement."""

    def process_single_file(self, file_path: Path, matched_criteria_ids: list, path: MonitoredPath) -> dict:
        """
        Process a single file: move it and record immediately in database.

        Returns:
            dict with processing results
        """
        result = {
            "success": False,
            "file_path": str(file_path),
            "error": None,
            "file_record_id": None
        }

        # Create a new database session for this thread
        db = SessionLocal()
        try:
            logger.debug(f"Processing single file: {file_path}")
            logger.debug(f"  Matched criteria IDs: {matched_criteria_ids}")

            source_base = Path(path.source_path)
            dest_base = Path(path.cold_storage_path)

            # Calculate destination path
            dest_path = FileMover.preserve_directory_structure(
                file_path, source_base, dest_base
            )
            logger.debug(f"  Destination: {dest_path}")

            # Check if file is already at destination
            if file_path.is_symlink():
                try:
                    symlink_target = file_path.readlink()
                    if symlink_target.is_absolute():
                        resolved_target = Path(symlink_target)
                    else:
                        resolved_target = (file_path.parent / symlink_target).resolve()

                    # If symlink points to destination, the file is already in cold storage
                    if resolved_target.resolve() == dest_path.resolve():
                        logger.debug(f"  Symlink points to destination - file already in cold storage")
                    else:
                        logger.debug(f"  Symlink points elsewhere - will process")
                except (OSError, RuntimeError) as e:
                    logger.debug(f"  Could not read symlink target: {e}, will process normally")
            else:
                # Regular file - check if already at destination
                if file_path.resolve() == dest_path.resolve():
                    logger.debug(f"  SKIPPED: File already at destination")
                    result["error"] = "File already at destination"
                    return result

            # Get original file stats BEFORE moving (critical for timestamp preservation)
            original_stat = None
            file_size = 0
            try:
                if file_path.is_symlink():
                    try:
                        actual_file = file_path.resolve(strict=True)
                        original_stat = actual_file.stat()
                        file_size = original_stat.st_size
                        logger.info(f"  TIMESTAMP CAPTURE (symlink->actual): atime={original_stat.st_atime} ({time.ctime(original_stat.st_atime)}), mtime={original_stat.st_mtime} ({time.ctime(original_stat.st_mtime)})")
                        logger.debug(f"  File is symlink, using actual file size: {file_size} bytes")
                    except (OSError, RuntimeError):
                        original_stat = file_path.stat()
                        file_size = original_stat.st_size
                        logger.info(f"  TIMESTAMP CAPTURE (symlink): atime={original_stat.st_atime} ({time.ctime(original_stat.st_atime)}), mtime={original_stat.st_mtime} ({time.ctime(original_stat.st_mtime)})")
                        logger.debug(f"  Could not resolve symlink, using symlink size: {file_size} bytes")
                else:
                    original_stat = file_path.stat()
                    file_size = original_stat.st_size
                    logger.info(f"  TIMESTAMP CAPTURE (regular file): atime={original_stat.st_atime} ({time.ctime(original_stat.st_atime)}), mtime={original_stat.st_mtime} ({time.ctime(original_stat.st_mtime)})")
                    logger.debug(f"  File size: {file_size} bytes")
            except (OSError, FileNotFoundError) as e:
                logger.debug(f"  Could not get file stats: {e}")
                result["error"] = f"Cannot stat source file: {e}"
                return result

            # Create progress callback for this file
            file_name = file_path.name
            operation = "move_to_cold"  # Default operation type

            def progress_callback(bytes_transferred: int):
                """Update progress for this file."""
                scan_progress_manager.update_file_progress(path.id, file_name, bytes_transferred)

            # Start tracking this file operation
            scan_progress_manager.start_file_operation(path.id, file_name, operation, file_size)

            # Move the file
            logger.debug(f"  Moving file using operation: {path.operation_type.value}")
            success, error = FileMover.move_file(
                file_path, dest_path, path.operation_type, path, progress_callback
            )

            if success:
                logger.debug(f"  File move successful")
                
                # CRITICAL: Sync timestamps to prevent immediate "Move Back" due to cross-fs copy
                # When moving between different filesystems (hot macOS -> cold Linux network mount),
                # shutil.copy2() creates a new file with current timestamps, causing the file to
                # immediately fail "older than X" criteria. We must preserve the original timestamps.
                try:
                    if original_stat and dest_path.exists():
                        # Check timestamps BEFORE syncing
                        pre_sync_stat = dest_path.stat()
                        logger.info(f"  BEFORE os.utime(): atime={pre_sync_stat.st_atime} ({time.ctime(pre_sync_stat.st_atime)}), mtime={pre_sync_stat.st_mtime} ({time.ctime(pre_sync_stat.st_mtime)})")

                        # Preserve original atime and mtime
                        logger.info(f"  CALLING os.utime() with: atime={original_stat.st_atime} ({time.ctime(original_stat.st_atime)}), mtime={original_stat.st_mtime} ({time.ctime(original_stat.st_mtime)})")
                        os.utime(dest_path, (original_stat.st_atime, original_stat.st_mtime))

                        # Verify timestamps AFTER syncing
                        post_sync_stat = dest_path.stat()
                        logger.info(f"  AFTER os.utime(): atime={post_sync_stat.st_atime} ({time.ctime(post_sync_stat.st_atime)}), mtime={post_sync_stat.st_mtime} ({time.ctime(post_sync_stat.st_mtime)})")

                        # Check if preservation worked
                        atime_diff = abs(post_sync_stat.st_atime - original_stat.st_atime)
                        mtime_diff = abs(post_sync_stat.st_mtime - original_stat.st_mtime)
                        if atime_diff > 1 or mtime_diff > 1:
                            logger.error(f"  TIMESTAMP PRESERVATION FAILED! atime diff={atime_diff}s, mtime diff={mtime_diff}s")
                        else:
                            logger.info(f"  TIMESTAMP PRESERVATION VERIFIED (atime diff={atime_diff}s, mtime diff={mtime_diff}s)")
                except (OSError, FileNotFoundError) as e:
                    logger.error(f"  Could not sync timestamps to {dest_path}: {e} (file may appear 'new' on next scan)")
                
                # Record the file immediately in database
                file_record_id = self._record_file_in_db(
                    db, path, file_path, dest_path, file_size, matched_criteria_ids
                )

                # FINAL VERIFICATION: Check if timestamps are still preserved after database operations
                final_stat = dest_path.stat()
                final_atime_diff = abs(final_stat.st_atime - original_stat.st_atime)
                if final_atime_diff > 1:
                    logger.error(f"  ATIME CORRUPTED AFTER DATABASE OPERATIONS! diff={final_atime_diff}s, final_atime={final_stat.st_atime} ({time.ctime(final_stat.st_atime)})")
                else:
                    logger.info(f"  Final verification: atime still preserved after DB operations")

                result["success"] = True
                result["file_record_id"] = file_record_id
                logger.info(f"Successfully processed and recorded: {file_path} -> {dest_path}")

                # Mark file operation as complete
                scan_progress_manager.complete_file_operation(path.id, file_name, operation, success=True)
            else:
                result["error"] = f"Failed to move {file_path}: {error}"
                logger.error(f"Failed to move {file_path}: {error}")

                # Mark file operation as failed
                scan_progress_manager.complete_file_operation(path.id, file_name, operation, success=False, error=error)

        except Exception as e:
            result["error"] = f"Error processing {file_path}: {str(e)}"
            logger.error(f"Error processing {file_path}: {str(e)}", exc_info=True)
        finally:
            db.close()

        return result

    def _record_file_in_db(self, db: Session, path: MonitoredPath, file_path: Path,
                          dest_path: Path, file_size: int, matched_criteria_ids: list) -> int:
        """
        Record a file in the database immediately after moving.
        Returns the file record ID.
        """
        # Check if a record already exists for this file
        existing_record = db.query(FileRecord).filter(
            (FileRecord.original_path == str(file_path)) |
            (FileRecord.cold_storage_path == str(dest_path))
        ).first()

        if existing_record:
            # Update existing record
            existing_record.cold_storage_path = str(dest_path)
            existing_record.file_size = file_size
            existing_record.operation_type = path.operation_type
            existing_record.criteria_matched = json.dumps(matched_criteria_ids)
            existing_record.path_id = path.id
            db.commit()
            logger.debug(f"Updated existing FileRecord ID: {existing_record.id}")
            file_record_id = existing_record.id
        else:
            # Create new record
            file_record = FileRecord(
                path_id=path.id,
                original_path=str(file_path),
                cold_storage_path=str(dest_path),
                file_size=file_size,
                operation_type=path.operation_type,
                criteria_matched=json.dumps(matched_criteria_ids)
            )
            db.add(file_record)
            db.commit()
            db.refresh(file_record)
            logger.debug(f"Created new FileRecord ID: {file_record.id}")
            file_record_id = file_record.id

        # Update file inventory to mark the hot storage entry as moved
        hot_inventory = db.query(FileInventory).filter(
            FileInventory.path_id == path.id,
            FileInventory.file_path == str(file_path),
            FileInventory.storage_type == "hot"
        ).first()

        if hot_inventory:
            # Mark the hot storage entry as moved
            hot_inventory.status = FileStatus.MOVED
            db.commit()
            logger.debug(f"Marked hot storage inventory entry as moved: {file_path}")

        return file_record_id

    def process_path(self, path: MonitoredPath, db: Session) -> dict:
        """
        Process a monitored path: scan, match, and move files.
        Also cleans up records for files that no longer exist.

        Returns:
            dict with scan results
        """
        # Start tracking scan progress
        scan_id = scan_progress_manager.start_scan(path.id, total_files=0)
        logger.info(f"Started scan progress tracking: {scan_id} for path {path.id}")

        try:
            # Check if path is in error state - if so, skip processing
            if path.error_message:
                logger.warning(
                    f"Path {path.name} (ID: {path.id}) is in error state and will not be processed. "
                    f"Error: {path.error_message}"
                )
                scan_progress_manager.finish_scan(path.id, status="failed")
                return {
                    "path_id": path.id,
                    "files_found": 0,
                    "files_moved": 0,
                    "files_cleaned": 0,
                    "errors": [f"Path is in error state: {path.error_message}"]
                }

            results = {
                "path_id": path.id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "files_skipped": 0,
                "total_scanned": 0,
                "errors": []
            }

            # First, clean up any missing files and duplicates for this path
            try:
                # Clean up missing files
                cleanup_results = FileCleanup.cleanup_missing_files(db, path_id=path.id)
                results["files_cleaned"] = cleanup_results["removed"]
                if cleanup_results["errors"]:
                    results["errors"].extend(cleanup_results["errors"])

                # Clean up duplicates
                duplicate_results = FileCleanup.cleanup_duplicates(db, path_id=path.id)
                results["files_cleaned"] += duplicate_results["removed"]
                if duplicate_results["errors"]:
                    results["errors"].extend(duplicate_results["errors"])
            except Exception as e:
                logger.warning(f"Error during cleanup for path {path.id}: {str(e)}")
                # Don't fail the scan if cleanup fails

            try:
                # Scan for matching files (pass db to check for pinned files)
                logger.debug(f"Processing path {path.id} ({path.name})")
                scan_results = FileScanner.scan_path(path, db)
                matching_files = scan_results["to_cold"]
                files_to_thaw = scan_results["to_hot"]
                results["files_found"] = len(matching_files)
                results["files_skipped"] = scan_results.get("skipped_hot", 0) + scan_results.get("skipped_cold", 0)
                results["total_scanned"] = scan_results.get("total_scanned", 0)
                logger.debug(
                    f"Path {path.id}: Scanned {results['total_scanned']} files, "
                    f"found {len(matching_files)} to move, {len(files_to_thaw)} to thaw, "
                    f"{results['files_skipped']} correctly placed"
                )

                # Update total files count for progress tracking
                total_files_to_process = len(matching_files) + len(files_to_thaw)
                scan_progress_manager.update_total_files(path.id, total_files_to_process)

                source_base = Path(path.source_path)
                dest_base = Path(path.cold_storage_path)

                # Process selective thawing for recently accessed files
                if files_to_thaw:
                    logger.info(f"Processing {len(files_to_thaw)} recently accessed files to thaw back to hot storage")

                    # Process thawing concurrently
                    max_workers = min(2, len(files_to_thaw))
                    logger.debug(f"Using {max_workers} worker threads for thawing {len(files_to_thaw)} files")
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_thaw = {
                            executor.submit(self._thaw_single_file, symlink_path, cold_storage_path):
                            (symlink_path, cold_storage_path)
                            for symlink_path, cold_storage_path in files_to_thaw
                        }

                        # Collect results as they complete
                        for future in as_completed(future_to_thaw):
                            symlink_path, cold_storage_path = future_to_thaw[future]
                            try:
                                thaw_result = future.result()
                                if thaw_result["success"]:
                                    results["files_moved"] += 1
                                    logger.debug(f"Successfully thawed recently accessed file: {cold_storage_path}")
                                else:
                                    results["errors"].append(thaw_result["error"])
                                    logger.error(f"Failed to thaw recently accessed file {cold_storage_path}: {thaw_result['error']}")
                            except Exception as e:
                                error_msg = f"Exception thawing recently accessed file {cold_storage_path}: {str(e)}"
                                results["errors"].append(error_msg)
                                logger.error(error_msg, exc_info=True)

                # Process files concurrently to record them immediately
                if matching_files:
                    logger.info(f"Processing {len(matching_files)} files concurrently for path {path.id}")

                    # Use ThreadPoolExecutor to process files concurrently
                    # Limit to 3 workers to avoid overwhelming the system
                    max_workers = min(3, len(matching_files))
                    logger.debug(f"Using {max_workers} worker threads for {len(matching_files)} files")
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        # Submit all file processing tasks
                        future_to_file = {
                            executor.submit(self.process_single_file, file_path, matched_criteria_ids, path):
                            (file_path, matched_criteria_ids)
                            for file_path, matched_criteria_ids in matching_files
                        }

                        # Collect results as they complete
                        for future in as_completed(future_to_file):
                            file_path, matched_criteria_ids = future_to_file[future]
                            try:
                                file_result = future.result()
                                if file_result["success"]:
                                    results["files_moved"] += 1
                                    logger.debug(f"Successfully processed file: {file_path}")
                                else:
                                    results["errors"].append(file_result["error"])
                                    logger.error(f"Failed to process file {file_path}: {file_result['error']}")
                            except Exception as e:
                                error_msg = f"Exception processing {file_path}: {str(e)}"
                                results["errors"].append(error_msg)
                                logger.error(error_msg, exc_info=True)

                    logger.info(f"Completed concurrent processing of {len(matching_files)} files for path {path.id}")

                # Reconcile missing symlinks for files in cold storage
                logger.info(f"Running symlink reconciliation for path {path.id}")
                try:
                    reconciliation_stats = FileReconciliation.reconcile_missing_symlinks(path, db)
                    if reconciliation_stats["symlinks_created"] > 0:
                        logger.info(
                            f"Reconciliation complete: Created {reconciliation_stats['symlinks_created']} missing symlinks"
                        )
                    if reconciliation_stats["errors"]:
                        results["errors"].extend(reconciliation_stats["errors"])
                except Exception as e:
                    error_msg = f"Error during symlink reconciliation for path {path.id}: {str(e)}"
                    logger.warning(error_msg)
                    results["errors"].append(error_msg)

            except Exception as e:
                error_msg = f"Error processing path {path.id}: {str(e)}"
                results["errors"].append(error_msg)
                logger.error(error_msg)
                # Mark scan as failed
                scan_progress_manager.finish_scan(path.id, status="failed")
                return results

            # Mark scan as completed
            scan_status = "completed" if not results["errors"] else "completed"  # Completed with errors is still completed
            scan_progress_manager.finish_scan(path.id, status=scan_status)
            logger.info(f"Scan {scan_id} finished with status: {scan_status}")

            return results
        except Exception as outer_e:
            # Catch any errors in the outer try block
            logger.error(f"Unexpected error in process_path for path {path.id}: {str(outer_e)}", exc_info=True)
            scan_progress_manager.finish_scan(path.id, status="failed")
            return {
                "path_id": path.id,
                "files_found": 0,
                "files_moved": 0,
                "files_cleaned": 0,
                "errors": [f"Unexpected error: {str(outer_e)}"]
            }

    def _thaw_single_file(self, symlink_path: Path, cold_storage_path: Path) -> dict:
        """
        Thaw a single file (move back from cold storage to hot storage) while preserving timestamps.
        Returns processing results.
        """
        result = {
            "success": False,
            "symlink_path": str(symlink_path),
            "cold_storage_path": str(cold_storage_path),
            "error": None
        }

        # Create a new database session for this thread
        db = SessionLocal()
        try:
            logger.debug(f"Thawing file: {cold_storage_path} -> {symlink_path}")

            # Remove the symlink
            if symlink_path.exists() and symlink_path.is_symlink():
                symlink_path.unlink()
                logger.debug(f"  Removed symlink: {symlink_path}")

            # Move the file back from cold storage to hot storage, preserving timestamps
            try:
                # Ensure destination directory exists
                symlink_path.parent.mkdir(parents=True, exist_ok=True)

                # Get original timestamps before moving
                stat_info = cold_storage_path.stat()

                # Try atomic rename first (same filesystem - preserves all timestamps)
                try:
                    cold_storage_path.rename(symlink_path)
                    logger.debug(f"  Moved file back (atomic rename): {cold_storage_path} -> {symlink_path}")
                except OSError:
                    # Cross-filesystem move - copy with timestamp preservation
                    shutil.copy2(str(cold_storage_path), str(symlink_path))
                    # Explicitly preserve timestamps
                    os.utime(str(symlink_path), ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns))
                    # Remove original file
                    cold_storage_path.unlink()
                    logger.debug(f"  Moved file back (cross-filesystem with timestamp preservation): {cold_storage_path} -> {symlink_path}")

                # Find and remove the FileRecord for this file
                file_record = db.query(FileRecord).filter(
                    FileRecord.cold_storage_path == str(cold_storage_path)
                ).first()

                if file_record:
                    db.delete(file_record)
                    db.commit()
                    result["success"] = True
                    logger.info(f"Removed FileRecord for thawed file: {cold_storage_path}")

                    # Update file inventory - the file should now be back in hot storage
                    # The inventory scanning will pick this up on the next scan
                else:
                    result["success"] = True
                    logger.debug(f"  No FileRecord found for {cold_storage_path}")
            except Exception as e:
                result["error"] = f"Failed to move file back {cold_storage_path}: {str(e)}"
                logger.error(result["error"])

        except Exception as e:
            result["error"] = f"Error thawing {cold_storage_path}: {str(e)}"
            logger.error(result["error"])
        finally:
            db.close()

        return result

