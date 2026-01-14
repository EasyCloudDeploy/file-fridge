"""API routes for path management."""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path
from app.database import get_db
from app.models import MonitoredPath, CriterionType, ColdStorageLocation
from app.schemas import MonitoredPathCreate, MonitoredPathUpdate, MonitoredPath as MonitoredPathSchema
from app.services.scheduler import scheduler_service
from app.services.scan_progress import scan_progress_manager
from app.services.path_migration import PathMigrationService
from app.utils.network_detection import check_atime_availability
from app.utils.indexing import IndexingManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/paths", tags=["paths"])


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
        c for c in path.criteria
        if c.enabled and c.criterion_type == CriterionType.ATIME
    ]

    if atime_criteria:
        # Check cold storage locations for network mount incompatibility
        error_locations = []

        for location in path.storage_locations:
            atime_available, error_msg = check_atime_availability(location.path)
            if not atime_available:
                error_locations.append(f"{location.name} ({location.path}): {error_msg}")

        if error_locations:
            # At least one storage location has atime issues
            combined_error = "ATIME criteria configured but not available on storage location(s): " + "; ".join(error_locations)
            path.error_message = combined_error
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=combined_error
            )

    # Clear error message if validation passes
    if path.error_message:
        path.error_message = None
        db.commit()


@router.get("", response_model=List[MonitoredPathSchema])
def list_paths(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all monitored paths."""
    paths = db.query(MonitoredPath).offset(skip).limit(limit).all()
    return paths


@router.post("", response_model=MonitoredPathSchema, status_code=status.HTTP_201_CREATED)
def create_path(path: MonitoredPathCreate, db: Session = Depends(get_db)):
    """Create a new monitored path."""
    # Validate source path exists
    source = Path(path.source_path)
    if not source.exists() or not source.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source path does not exist or is not a directory: {path.source_path}"
        )

    # Check for duplicate name
    existing = db.query(MonitoredPath).filter(MonitoredPath.name == path.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path with name '{path.name}' already exists"
        )

    # Fetch and associate storage locations
    storage_locations = db.query(ColdStorageLocation).filter(
        ColdStorageLocation.id.in_(path.storage_location_ids)
    ).all()

    if len(storage_locations) != len(path.storage_location_ids):
        found_ids = {loc.id for loc in storage_locations}
        missing_ids = set(path.storage_location_ids) - found_ids
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location IDs not found: {missing_ids}"
        )

    # Create path without storage_location_ids (not a DB column)
    path_data = path.model_dump(exclude={'storage_location_ids'})
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
            db_path.source_path,
            location.path,
            db_path.prevent_indexing
        )

    # Add to scheduler
    if db_path.enabled:
        scheduler_service.add_path_job(db_path)

    return db_path


@router.get("/{path_id}", response_model=MonitoredPathSchema)
def get_path(path_id: int, db: Session = Depends(get_db)):
    """Get a specific monitored path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    return path


@router.put("/{path_id}", response_model=MonitoredPathSchema)
def update_path(
    path_id: int,
    path_update: MonitoredPathUpdate,
    confirm_cold_storage_change: bool = Query(False, description="Confirm cold storage path change"),
    migration_action: str = Query(None, description="Migration action: 'move' or 'abandon'"),
    db: Session = Depends(get_db)
):
    """Update a monitored path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )

    # Validate paths if being updated
    update_data = path_update.model_dump(exclude_unset=True)

    if "source_path" in update_data:
        source = Path(update_data["source_path"])
        if not source.exists() or not source.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Source path does not exist or is not a directory: {update_data['source_path']}"
            )
    
    # Check for duplicate name if name is being updated
    if "name" in update_data:
        existing = db.query(MonitoredPath).filter(
            MonitoredPath.name == update_data["name"],
            MonitoredPath.id != path_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path with name '{update_data['name']}' already exists"
            )

    # Handle storage_location_ids update if provided
    storage_location_ids = update_data.pop('storage_location_ids', None)
    if storage_location_ids is not None:
        storage_locations = db.query(ColdStorageLocation).filter(
            ColdStorageLocation.id.in_(storage_location_ids)
        ).all()

        if len(storage_locations) != len(storage_location_ids):
            found_ids = {loc.id for loc in storage_locations}
            missing_ids = set(storage_location_ids) - found_ids
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Storage location IDs not found: {missing_ids}"
            )

        path.storage_locations = storage_locations

    # Update fields
    for field, value in update_data.items():
        setattr(path, field, value)

    db.commit()
    db.refresh(path)

    # Validate path configuration (check for atime + network mount incompatibility)
    validate_path_configuration(path, db)

    # Manage .noindex files if prevent_indexing or paths were updated
    if "prevent_indexing" in update_data or "source_path" in update_data or storage_location_ids is not None:
        for location in path.storage_locations:
            IndexingManager.manage_noindex_files(
                path.source_path,
                location.path,
                path.prevent_indexing
            )

    # Update scheduler job
    scheduler_service.remove_path_job(path_id)
    if path.enabled:
        scheduler_service.add_path_job(path)

    return path


@router.delete("/{path_id}", status_code=status.HTTP_200_OK)
def delete_path(
    path_id: int,
    undo_operations: bool = Query(False, description="If True, move all files back from cold storage before deleting"),
    db: Session = Depends(get_db)
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    results = {
        "path_id": path_id,
        "undo_operations": undo_operations,
        "files_reversed": 0,
        "errors": []
    }
    
    # If undo_operations is True, reverse all file operations first
    if undo_operations:
        from app.services.path_reverser import PathReverser
        logger.info(f"Undoing operations for path {path_id} before deletion")
        reverse_results = PathReverser.reverse_path_operations(path_id, db)
        results["files_reversed"] = reverse_results["files_reversed"]
        results["errors"] = reverse_results["errors"]
    
    # Remove from scheduler
    scheduler_service.remove_path_job(path_id)
    
    # Delete the path
    db.delete(path)
    db.commit()
    
    return results


@router.post("/{path_id}/scan", status_code=status.HTTP_202_ACCEPTED)
def trigger_scan(path_id: int, db: Session = Depends(get_db)):
    """Manually trigger a scan for a path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )

    # Check if a scan is already running
    if scan_progress_manager.is_scan_running(path_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A scan is already running for path {path_id}"
        )

    # Trigger scan asynchronously
    scheduler_service.trigger_scan(path_id)

    return {"message": f"Scan triggered for path {path_id}"}


@router.get("/{path_id}/scan/progress")
def get_scan_progress(path_id: int, db: Session = Depends(get_db)):
    """
    Get real-time progress of the current scan for a path.

    Returns progress information including:
    - Overall progress (files processed / total files)
    - Current file operations in progress
    - Errors encountered
    - Scan status (running, completed, failed)
    """
    # Verify path exists
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )

    # Get progress from manager
    progress = scan_progress_manager.get_progress(path_id)

    if progress is None:
        # No active or recent scan
        return {
            "scan_id": None,
            "path_id": path_id,
            "status": "idle",
            "progress": {
                "total_files": 0,
                "files_processed": 0,
                "files_moved_to_cold": 0,
                "files_moved_to_hot": 0,
                "files_skipped": 0,
                "percent": 0
            },
            "current_operations": [],
            "errors": []
        }

    return progress

