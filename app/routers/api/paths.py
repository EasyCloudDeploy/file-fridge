# ruff: noqa: B008
"""API routes for path management."""

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import schemas
from app.database import get_db
from app.models import ColdStorageLocation, CriterionType, FileInventory, MonitoredPath, User
from app.security import get_current_user
from app.services.scan_progress import scan_progress_manager
from app.services.scheduler import scheduler_service
from app.utils.indexing import IndexingManager
from app.utils.network_detection import check_atime_availability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/paths", tags=["paths"])


def validate_path_access(path: str, path_id: Optional[int] = None) -> Tuple[bool, str]:
    """
    Validate that a path exists, is a directory, and has read/write/execute permissions.

    Args:
        path: The path to validate
        path_id: Optional path ID for better error messages

    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    path_obj = Path(path)

    # Check existence
    if not path_obj.exists():
        context = f" (path_id={path_id})" if path_id else ""
        return False, f"Path does not exist{context}: {path}"

    # Check if directory
    if not path_obj.is_dir():
        context = f" (path_id={path_id})" if path_id else ""
        return False, f"Path is not a directory{context}: {path}"

    # Check permissions (read, write, execute)
    # Execute permission is needed to list directory contents
    if not os.access(path, os.R_OK):
        context = f" (path_id={path_id})" if path_id else ""
        return False, f"Path is not readable - check permissions{context}: {path}"

    if not os.access(path, os.W_OK):
        context = f" (path_id={path_id})" if path_id else ""
        return False, f"Path is not writable - check permissions{context}: {path}"

    if not os.access(path, os.X_OK):
        context = f" (path_id={path_id})" if path_id else ""
        return (
            False,
            f"Path is not executable (cannot list contents) - check permissions{context}: {path}",
        )

    return True, ""


def validate_path_configuration(path: MonitoredPath, db: Session) -> None:
    """
    Validate path configuration and check for incompatible settings.

    Sets error_message on the path if validation fails.

    Args:
        path: The MonitoredPath to validate
        db: Database session
    """
    # Check if any enabled criteria use ATIME
    atime_criteria = [
        c for c in path.criteria if c.enabled and c.criterion_type == CriterionType.ATIME
    ]

    if atime_criteria:
        error_locations = []

        # Check source path for ATIME availability (critical for scanning)
        atime_available, error_msg = check_atime_availability(path.source_path)
        if not atime_available:
            error_locations.append(f"Source path ({path.source_path}): {error_msg}")

        # Check cold storage locations for network mount incompatibility
        for location in path.storage_locations:
            atime_available, error_msg = check_atime_availability(location.path)
            if not atime_available:
                error_locations.append(f"{location.name} ({location.path}): {error_msg}")

        if error_locations:
            # At least one location has atime issues
            combined_error = (
                f"ATIME criteria configured but not available on path (path_id={path.id}). "
                f"Issues: {'; '.join(error_locations)}"
            )
            path.error_message = combined_error
            db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=combined_error)

    # Clear error message if validation passes
    if path.error_message:
        path.error_message = None
        db.commit()


@router.get("", response_model=List[schemas.MonitoredPathSummary])
def list_paths(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all monitored paths with a summary of their contents."""
    file_count_subquery = (
        select(func.count(FileInventory.id))
        .where(FileInventory.path_id == MonitoredPath.id)
        .correlate(MonitoredPath)
        .scalar_subquery()
    )

    paths_with_counts = (
        db.query(MonitoredPath, file_count_subquery.label("file_count"))
        .options(selectinload(MonitoredPath.storage_locations))
        .order_by(MonitoredPath.name)
        .offset(skip)
        .limit(limit)
        .all()
    )

    result = []
    for path, file_count in paths_with_counts:
        summary = schemas.MonitoredPathSummary(
            **{k: v for k, v in path.__dict__.items() if not k.startswith("_")},
            file_count=file_count,
            is_path_present=Path(path.source_path).exists(),
        )
        result.append(summary)
    return result


@router.get("/stats", response_model=List[schemas.StorageStats])
def get_hot_storage_stats(db: Session = Depends(get_db)):
    """Get storage statistics for all monitored paths (hot storage)."""
    paths = db.query(MonitoredPath).all()

    unique_volumes = {}
    for path in paths:
        path_str = path.source_path
        try:
            # Validate path is a directory before attempting to stat
            path_obj = Path(path_str)
            if not path_obj.exists():
                if "not_found" not in unique_volumes:
                    unique_volumes["not_found"] = []
                unique_volumes["not_found"].append(path_str)
                continue

            if not path_obj.is_dir():
                if "error" not in unique_volumes:
                    unique_volumes["error"] = []
                unique_volumes["error"].append((path_str, "Not a directory"))
                continue

            # Get the device ID for the path
            device_id = Path(path_str).stat().st_dev
            if device_id not in unique_volumes:
                unique_volumes[device_id] = path_str
        except FileNotFoundError:
            # Handle cases where the path doesn't exist
            if "not_found" not in unique_volumes:
                unique_volumes["not_found"] = []
            unique_volumes["not_found"].append(path_str)
        except PermissionError:
            logger.exception(f"Permission denied accessing path {path_str}")
            if "error" not in unique_volumes:
                unique_volumes["error"] = []
            unique_volumes["error"].append((path_str, "Permission denied"))
        except Exception as e:
            # Handle other potential errors
            logger.exception(f"Error stating path {path_str}")
            if "error" not in unique_volumes:
                unique_volumes["error"] = []
            unique_volumes["error"].append((path_str, str(e)))

    stats_list = []
    for device_id, path_info in unique_volumes.items():
        if device_id == "not_found":
            for p in path_info:
                stats_list.append(
                    schemas.StorageStats(
                        path=p, total_bytes=0, used_bytes=0, free_bytes=0, error="Path not found"
                    )
                )
            continue

        if device_id == "error":
            for p, err_msg in path_info:
                stats_list.append(
                    schemas.StorageStats(
                        path=p, total_bytes=0, used_bytes=0, free_bytes=0, error=err_msg
                    )
                )
            continue

        # path_info is a string for valid device_ids
        path_str = path_info
        try:
            total, used, free = shutil.disk_usage(path_str)
            stats_list.append(
                schemas.StorageStats(
                    path=path_str,
                    total_bytes=total,
                    used_bytes=used,
                    free_bytes=free,
                )
            )
        except Exception as e:
            logger.exception(f"Error getting disk usage for {path_str}")
            stats_list.append(
                schemas.StorageStats(
                    path=path_str,
                    total_bytes=0,
                    used_bytes=0,
                    free_bytes=0,
                    error=f"Disk usage error: {e}",
                )
            )

    return stats_list


@router.post("", response_model=schemas.MonitoredPath, status_code=status.HTTP_201_CREATED)
def create_path(
    path: schemas.MonitoredPathCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new monitored path."""
    # Validate source path exists and has proper permissions
    is_valid, error_msg = validate_path_access(path.source_path)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    # Check for duplicate name
    existing = db.query(MonitoredPath).filter(MonitoredPath.name == path.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path with name '{path.name}' already exists",
        )

    # Fetch and associate storage locations
    storage_locations = (
        db.query(ColdStorageLocation)
        .filter(ColdStorageLocation.id.in_(path.storage_location_ids))
        .all()
    )

    if len(storage_locations) != len(path.storage_location_ids):
        found_ids = {loc.id for loc in storage_locations}
        missing_ids = set(path.storage_location_ids) - found_ids
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location IDs not found: {missing_ids}",
        )

    # Create path without storage_location_ids (not a DB column)
    path_data = path.model_dump(exclude={"storage_location_ids"})
    db_path = MonitoredPath(**path_data)
    db_path.storage_locations = storage_locations

    db.add(db_path)
    db.commit()
    db.refresh(db_path)

    # Validate path configuration (check for atime + network mount incompatibility)
    try:
        validate_path_configuration(db_path, db)
    except HTTPException:
        # If validation fails, remove the path we just created
        db.delete(db_path)
        db.commit()
        raise

    # Manage .noindex files based on prevent_indexing setting
    for location in db_path.storage_locations:
        IndexingManager.manage_noindex_files(
            db_path.source_path, location.path, db_path.prevent_indexing
        )

    # Add to scheduler
    if db_path.enabled:
        scheduler_service.add_path_job(db_path)

    # Dispatch PATH_CREATED event
    try:
        from app.services.notification_events import NotificationEventType, PathCreatedData
        from app.services.notification_service import NotificationService

        notification_service = NotificationService()
        notification_service.dispatch_event_sync(
            db=db,
            event_type=NotificationEventType.PATH_CREATED,
            event_data=PathCreatedData(
                path_id=db_path.id,
                path_name=db_path.name,
                source_path=db_path.source_path,
                operation_type=db_path.operation_type.value,
                created_by=current_user.username,
            ),
        )
    except Exception as e:
        logger.error(f"Failed to dispatch PATH_CREATED event: {e}")
        # Don't fail the request - notification is non-critical

    return db_path


@router.get("/{path_id}", response_model=schemas.MonitoredPathSummary)
def get_path(path_id: int, db: Session = Depends(get_db)):
    """Get a single monitored path with a summary of its contents."""
    db_path = (
        db.query(MonitoredPath)
        .options(selectinload(MonitoredPath.storage_locations))
        .filter(MonitoredPath.id == path_id)
        .first()
    )
    if db_path is None:
        raise HTTPException(status_code=404, detail="Path not found")

    file_count = (
        db.query(func.count(FileInventory.id)).filter(FileInventory.path_id == path_id).scalar()
    )

    return schemas.MonitoredPathSummary(
        **{k: v for k, v in db_path.__dict__.items() if not k.startswith("_")},
        file_count=file_count,
        is_path_present=Path(db_path.source_path).exists(),
    )


@router.put("/{path_id}", response_model=schemas.MonitoredPath)
def update_path(
    path_id: int,
    path_update: schemas.MonitoredPathUpdate,
    confirm_cold_storage_change: bool = Query(
        False, description="Confirm cold storage path change"
    ),
    migration_action: str = Query(None, description="Migration action: 'move' or 'abandon'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a monitored path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )

    # Validate paths if being updated
    update_data = path_update.model_dump(exclude_unset=True)

    if "source_path" in update_data:
        is_valid, error_msg = validate_path_access(update_data["source_path"], path_id)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    # Check for duplicate name if name is being updated
    if "name" in update_data:
        existing = (
            db.query(MonitoredPath)
            .filter(MonitoredPath.name == update_data["name"], MonitoredPath.id != path_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path with name '{update_data['name']}' already exists",
            )

    # Track changes for event notification
    changes = {}

    # Handle storage_location_ids update if provided
    storage_location_ids = update_data.pop("storage_location_ids", None)
    if storage_location_ids is not None:
        storage_locations = (
            db.query(ColdStorageLocation)
            .filter(ColdStorageLocation.id.in_(storage_location_ids))
            .all()
        )

        if len(storage_locations) != len(storage_location_ids):
            found_ids = {loc.id for loc in storage_locations}
            missing_ids = set(storage_location_ids) - found_ids
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Storage location IDs not found: {missing_ids}",
            )

        path.storage_locations = storage_locations
        changes["storage_location_ids"] = storage_location_ids

    # Update fields
    for field, value in update_data.items():
        old_value = getattr(path, field)
        if old_value != value:
            changes[field] = value
        setattr(path, field, value)

    db.commit()
    db.refresh(path)

    # Validate path configuration (check for atime + network mount incompatibility)
    validate_path_configuration(path, db)

    # Manage .noindex files if prevent_indexing or paths were updated
    if (
        "prevent_indexing" in update_data
        or "source_path" in update_data
        or storage_location_ids is not None
    ):
        for location in path.storage_locations:
            IndexingManager.manage_noindex_files(
                path.source_path, location.path, path.prevent_indexing
            )

    # Update scheduler job
    scheduler_service.remove_path_job(path_id)
    if path.enabled:
        scheduler_service.add_path_job(path)

    # Dispatch PATH_UPDATED event (only if changes occurred)
    if changes:
        try:
            from app.services.notification_events import NotificationEventType, PathUpdatedData
            from app.services.notification_service import NotificationService

            notification_service = NotificationService()
            notification_service.dispatch_event_sync(
                db=db,
                event_type=NotificationEventType.PATH_UPDATED,
                event_data=PathUpdatedData(
                    path_id=path.id,
                    path_name=path.name,
                    changes=changes,
                    updated_by=current_user.username,
                ),
            )
        except Exception as e:
            logger.error(f"Failed to dispatch PATH_UPDATED event: {e}")
            # Don't fail the request - notification is non-critical

    return path


@router.delete("/{path_id}", status_code=status.HTTP_200_OK)
def delete_path(
    path_id: int,
    undo_operations: bool = Query(
        False, description="If True, move all files back from cold storage before deleting"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a monitored path.

    Args:
        path_id: The path ID to delete
        undo_operations: If True, move all files back from cold storage before deleting
    """

    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )

    results = {
        "path_id": path_id,
        "undo_operations": undo_operations,
        "files_reversed": 0,
        "errors": [],
    }

    # If undo_operations is True, reverse all file operations first
    if undo_operations:
        from app.services.path_reverser import PathReverser

        logger.info(f"Undoing operations for path {path_id} before deletion")
        reverse_results = PathReverser.reverse_path_operations(path_id, db)
        results["files_reversed"] = reverse_results["files_reversed"]
        results["errors"] = reverse_results["errors"]

    # Store data for event before deletion
    path_name = path.name
    source_path = path.source_path

    # Remove from scheduler
    scheduler_service.remove_path_job(path_id)

    # Delete the path
    db.delete(path)
    db.commit()

    # Dispatch PATH_DELETED event
    try:
        from app.services.notification_events import NotificationEventType, PathDeletedData
        from app.services.notification_service import NotificationService

        notification_service = NotificationService()
        notification_service.dispatch_event_sync(
            db=db,
            event_type=NotificationEventType.PATH_DELETED,
            event_data=PathDeletedData(
                path_id=path_id,
                path_name=path_name,
                source_path=source_path,
                deleted_by=current_user.username,
            ),
        )
    except Exception as e:
        logger.error(f"Failed to dispatch PATH_DELETED event: {e}")
        # Don't fail the request - notification is non-critical

    return results


@router.post("/{path_id}/scan", status_code=status.HTTP_202_ACCEPTED)
def trigger_scan(path_id: int, db: Session = Depends(get_db)):
    """
    Manually trigger a scan for a path.

    Note: If a scan is already running, this will return 409 Conflict.
    The scan process itself has built-in protection against concurrent scans.
    """
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )

    # Validate path accessibility before triggering scan
    is_valid, error_msg = validate_path_access(path.source_path, path_id)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot scan path: {error_msg}"
        )

    # Check if a scan is already running
    # Note: This is a best-effort check; the scan_progress_manager.start_scan()
    # method provides atomic protection against concurrent scans
    if scan_progress_manager.is_scan_running(path_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A scan is already running for path_id={path_id} ({path.name}). "
            f"Use GET /api/v1/paths/{path_id}/scan/progress to monitor progress.",
        )

    # Trigger scan asynchronously
    # The scheduler service will call scan_progress_manager.start_scan() which
    # provides atomic protection against race conditions
    scheduler_service.trigger_scan(path_id)

    return {
        "message": f"Scan triggered for path_id={path_id} ({path.name})",
        "path_id": path_id,
        "path_name": path.name,
    }


@router.get("/{path_id}/scan/progress")
def get_scan_progress(path_id: int, db: Session = Depends(get_db)):
    """
    Get real-time progress of the current scan for a path.

    Returns progress information including:
    - Overall progress (files processed / total files)
    - Current file operations in progress
    - Errors encountered
    - Scan status (running, completed, failed, idle)
    """
    # Verify path exists
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )

    # Get progress from manager
    progress = scan_progress_manager.get_progress(path_id)

    if progress is None:
        # No active or recent scan - return idle state with path context
        return {
            "scan_id": None,
            "path_id": path_id,
            "path_name": path.name,
            "status": "idle",
            "message": f"No active or recent scan for path_id={path_id} ({path.name})",
            "progress": {
                "total_files": 0,
                "files_processed": 0,
                "files_moved_to_cold": 0,
                "files_moved_to_hot": 0,
                "files_skipped": 0,
                "percent": 0,
            },
            "current_operations": [],
            "errors": [],
        }

    # Add path name to progress response for better UX
    if isinstance(progress, dict):
        progress["path_name"] = path.name

    return progress


@router.get("/{path_id}/scan-errors", response_model=schemas.PathScanErrors)
def get_scan_errors(path_id: int, db: Session = Depends(get_db)):
    """
    Get the error log from the last scan for a path.

    This endpoint is used for lazy-loading error details in the UI.
    The error log is not included in the list view to keep payloads light.
    """
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )

    return schemas.PathScanErrors(
        path_id=path.id,
        path_name=path.name,
        last_scan_at=path.last_scan_at,
        last_scan_status=path.last_scan_status,
        last_scan_error_log=path.last_scan_error_log,
    )
