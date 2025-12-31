"""Processes scans and moves files."""
import logging
import json
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
            matching_files = FileScanner.scan_path(path, db)
            results["files_found"] = len(matching_files)
            
            source_base = Path(path.source_path)
            dest_base = Path(path.cold_storage_path)
            
            # Process each matching file
            for file_path, matched_criteria_ids in matching_files:
                try:
                    # Calculate destination path
                    dest_path = FileMover.preserve_directory_structure(
                        file_path, source_base, dest_base
                    )
                    
                    # Skip if already in cold storage or if it's a symlink
                    if file_path.is_symlink():
                        continue
                    # Skip if destination is the same as source (already moved)
                    if file_path.resolve() == dest_path.resolve():
                        continue
                    
                    # Get file size before moving
                    try:
                        file_size = file_path.stat().st_size
                    except (OSError, FileNotFoundError):
                        file_size = 0
                    
                    # Move the file
                    success, error = FileMover.move_file(
                        file_path, dest_path, path.operation_type, path
                    )
                    
                    if success:
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
                        
                        results["files_moved"] += 1
                    else:
                        results["errors"].append(f"Failed to move {file_path}: {error}")
                        logger.error(f"Failed to move {file_path}: {error}")
                
                except Exception as e:
                    error_msg = f"Error processing {file_path}: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)
            
            db.commit()
        
        except Exception as e:
            error_msg = f"Error processing path {path.id}: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
            db.rollback()
        
        return results

