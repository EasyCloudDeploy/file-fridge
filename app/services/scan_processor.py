"""Processes scans and moves files."""
import logging
import json
import shutil
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import MonitoredPath, FileRecord, OperationType
from app.services.file_scanner import FileScanner
from app.services.file_mover import FileMover
from app.services.file_cleanup import FileCleanup
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

            # Get file size before moving
            try:
                if file_path.is_symlink():
                    try:
                        actual_file = file_path.resolve(strict=True)
                        file_size = actual_file.stat().st_size
                        logger.debug(f"  File is symlink, using actual file size: {file_size} bytes")
                    except (OSError, RuntimeError):
                        file_size = file_path.stat().st_size
                        logger.debug(f"  Could not resolve symlink, using symlink size: {file_size} bytes")
                else:
                    file_size = file_path.stat().st_size
                    logger.debug(f"  File size: {file_size} bytes")
            except (OSError, FileNotFoundError) as e:
                file_size = 0
                logger.debug(f"  Could not get file size: {e}")

            # Move the file
            logger.debug(f"  Moving file using operation: {path.operation_type.value}")
            success, error = FileMover.move_file(
                file_path, dest_path, path.operation_type, path
            )

            if success:
                logger.debug(f"  File move successful")
                # Record the file immediately in database
                file_record_id = self._record_file_in_db(
                    db, path, file_path, dest_path, file_size, matched_criteria_ids
                )
                result["success"] = True
                result["file_record_id"] = file_record_id
                logger.info(f"Successfully processed and recorded: {file_path} -> {dest_path}")
            else:
                result["error"] = f"Failed to move {file_path}: {error}"
                logger.error(f"Failed to move {file_path}: {error}")

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
            return existing_record.id
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
            return file_record.id

    def process_path(self, path: MonitoredPath, db: Session) -> dict:
        """
        Process a monitored path: scan, match, and move files.
        Also cleans up records for files that no longer exist.
        
        Returns:
            dict with scan results
        """
        results = {
            "path_id": path.id,
            "files_found": 0,
            "files_moved": 0,
            "files_cleaned": 0,
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
            logger.debug(f"Path {path.id}: Found {len(matching_files)} files matching criteria, {len(files_to_thaw)} files to move back")
            
            source_base = Path(path.source_path)
            dest_base = Path(path.cold_storage_path)

            # First, handle files that need to be moved back from cold storage
            if files_to_thaw:
                logger.info(f"Processing {len(files_to_thaw)} files to thaw for path {path.id}")

                # Process thawing concurrently
                with ThreadPoolExecutor(max_workers=min(3, len(files_to_thaw))) as executor:
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
                                logger.debug(f"Successfully thawed file: {cold_storage_path}")
                            else:
                                results["errors"].append(thaw_result["error"])
                                logger.error(f"Failed to thaw file {cold_storage_path}: {thaw_result['error']}")
                        except Exception as e:
                            error_msg = f"Exception thawing {cold_storage_path}: {str(e)}"
                            results["errors"].append(error_msg)
                            logger.error(error_msg, exc_info=True)

            # Process files concurrently to record them immediately
            if matching_files:
                logger.info(f"Processing {len(matching_files)} files concurrently for path {path.id}")

                # Use ThreadPoolExecutor to process files concurrently
                with ThreadPoolExecutor(max_workers=min(5, len(matching_files))) as executor:
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

        except Exception as e:
            error_msg = f"Error processing path {path.id}: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)

        return results

    def _thaw_single_file(self, symlink_path: Path, cold_storage_path: Path) -> dict:
        """
        Thaw a single file (move back from cold storage to hot storage).
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

            # Move the file back from cold storage to hot storage
            try:
                # Ensure destination directory exists
                symlink_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cold_storage_path), str(symlink_path))
                logger.debug(f"  Moved file back: {cold_storage_path} -> {symlink_path}")

                # Find and remove the FileRecord for this file
                file_record = db.query(FileRecord).filter(
                    FileRecord.cold_storage_path == str(cold_storage_path)
                ).first()

                if file_record:
                    db.delete(file_record)
                    db.commit()
                    result["success"] = True
                    logger.info(f"Removed FileRecord for thawed file: {cold_storage_path}")
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

