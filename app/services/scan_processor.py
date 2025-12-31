"""Processes scans and moves files."""
import logging
import json
import shutil
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import MonitoredPath, FileRecord, OperationType
from app.services.file_scanner import FileScanner
from app.services.file_mover import FileMover
from app.services.file_cleanup import FileCleanup

logger = logging.getLogger(__name__)


class ScanProcessor:
    """Processes file scans and handles file movement."""
    
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
            for symlink_path, cold_storage_path in files_to_thaw:
                try:
                    logger.debug(f"Moving file back from cold storage: {cold_storage_path} -> {symlink_path}")
                    
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
                            logger.info(f"Removed FileRecord for thawed file: {cold_storage_path}")
                            results["files_moved"] += 1
                        else:
                            logger.debug(f"  No FileRecord found for {cold_storage_path}")
                    except Exception as e:
                        error_msg = f"Failed to move file back {cold_storage_path}: {str(e)}"
                        results["errors"].append(error_msg)
                        logger.error(error_msg)
                except Exception as e:
                    error_msg = f"Error moving file back {cold_storage_path}: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)
            
            # Process each matching file (move to cold storage)
            for file_path, matched_criteria_ids in matching_files:
                try:
                    logger.debug(f"Processing file: {file_path}")
                    logger.debug(f"  Matched criteria IDs: {matched_criteria_ids}")
                    
                    # Calculate destination path
                    dest_path = FileMover.preserve_directory_structure(
                        file_path, source_base, dest_base
                    )
                    logger.debug(f"  Destination: {dest_path}")
                    
                    # Check if file is already at destination
                    # For symlinks, we need to check the symlink target, not resolve() which follows the symlink
                    # For regular files, check if the file is already at the destination
                    if file_path.is_symlink():
                        try:
                            # Read the symlink target (doesn't follow the link)
                            symlink_target = file_path.readlink()
                            logger.debug(f"  File is a symlink, target: {symlink_target}")
                            
                            # Resolve to absolute path for comparison
                            if symlink_target.is_absolute():
                                resolved_target = Path(symlink_target)
                            else:
                                # Relative symlink - resolve relative to symlink's parent
                                resolved_target = (file_path.parent / symlink_target).resolve()
                            
                            logger.debug(f"  Resolved symlink target: {resolved_target}")
                            logger.debug(f"  Destination path: {dest_path.resolve()}")
                            
                            # If symlink points to destination, the file is already in cold storage
                            # We should still process it - FileMover will remove the symlink
                            # and we'll create/update the FileRecord
                            if resolved_target.resolve() == dest_path.resolve():
                                logger.debug(f"  Symlink points to destination ({resolved_target}) - file already in cold storage")
                                logger.debug(f"  Will process to remove symlink and update record")
                                # Continue processing - don't skip
                            else:
                                logger.debug(f"  Symlink points to {resolved_target}, destination is {dest_path} - will process")
                        except (OSError, RuntimeError) as e:
                            logger.debug(f"  Could not read symlink target: {e}, will process normally")
                    else:
                        # Regular file - check if already at destination
                        if file_path.resolve() == dest_path.resolve():
                            logger.debug(f"  SKIPPED: File already at destination")
                            continue
                    
                    # Get file size before moving
                    # For symlinks, get size from the actual file, not the symlink
                    try:
                        if file_path.is_symlink():
                            # Get size from the actual file (symlink target)
                            try:
                                actual_file = file_path.resolve(strict=True)
                                file_size = actual_file.stat().st_size
                                logger.debug(f"  File is symlink, using actual file size: {file_size} bytes ({file_size / 1024 / 1024:.2f} MB)")
                            except (OSError, RuntimeError):
                                # Fall back to symlink itself if resolution fails
                                file_size = file_path.stat().st_size
                                logger.debug(f"  Could not resolve symlink, using symlink size: {file_size} bytes")
                        else:
                            file_size = file_path.stat().st_size
                            logger.debug(f"  File size: {file_size} bytes ({file_size / 1024 / 1024:.2f} MB)")
                    except (OSError, FileNotFoundError) as e:
                        file_size = 0
                        logger.debug(f"  Could not get file size: {e}")
                    
                    # Move the file
                    # FileMover will handle symlinks correctly:
                    # - If symlink points to destination, it will just remove the symlink
                    # - If symlink points elsewhere, it will move the actual file
                    logger.debug(f"  Moving file using operation: {path.operation_type.value}")
                    if file_path.is_symlink():
                        logger.debug(f"  File is a symlink - FileMover will handle appropriately")
                    
                    success, error = FileMover.move_file(
                        file_path, dest_path, path.operation_type, path
                    )
                    
                    if success:
                        logger.debug(f"  File move successful")
                        # Check if a record already exists for this file
                        # Check by original_path or cold_storage_path to prevent duplicates
                        existing_record = db.query(FileRecord).filter(
                            (FileRecord.original_path == str(file_path)) |
                            (FileRecord.cold_storage_path == str(dest_path))
                        ).first()
                        
                        if existing_record:
                            # Update existing record instead of creating duplicate
                            existing_record.cold_storage_path = str(dest_path)
                            existing_record.file_size = file_size
                            existing_record.operation_type = path.operation_type
                            existing_record.criteria_matched = json.dumps(matched_criteria_ids)
                            existing_record.path_id = path.id
                            logger.info(f"Updated existing FileRecord for: {file_path} -> {dest_path}")
                            logger.debug(f"  Updated FileRecord ID: {existing_record.id}")
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
                            logger.info(f"Created new FileRecord for: {file_path} -> {dest_path}")
                            logger.debug(f"  New FileRecord will be created")
                        
                        results["files_moved"] += 1
                    else:
                        results["errors"].append(f"Failed to move {file_path}: {error}")
                        logger.error(f"Failed to move {file_path}: {error}")
                        logger.debug(f"  Move operation failed: {error}")
                
                except Exception as e:
                    error_msg = f"Error processing {file_path}: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)
                    logger.debug(f"  Exception details: {e}", exc_info=True)
            
            db.commit()
        
        except Exception as e:
            error_msg = f"Error processing path {path.id}: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
            db.rollback()
        
        return results

