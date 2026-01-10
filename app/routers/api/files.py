"""API routes for file management."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from typing import List, Optional
from pathlib import Path
from datetime import datetime
from starlette.concurrency import run_in_threadpool
from app.database import get_db
from app.models import FileRecord, MonitoredPath, FileInventory, StorageType, FileStatus, ColdStorageLocation
import logging
from app.schemas import FileInventory as FileInventorySchema, FileMoveRequest, FileRelocateRequest, StorageType as StorageTypeSchema, PaginatedFileInventory
from app.services.file_mover import FileMover
from app.services.file_thawer import FileThawer
from app.models import OperationType
import math

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("")
async def list_files(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    path_id: Optional[int] = Query(None, description="Filter by monitored path ID"),
    storage_type: Optional[StorageTypeSchema] = Query(None, description="Filter by storage type (hot/cold)"),
    status: Optional[str] = Query(None, description="Filter by file status"),
    search: Optional[str] = Query(None, description="Search in file path"),
    extension: Optional[str] = Query(None, description="Filter by file extension (e.g., .pdf, .jpg)"),
    mime_type: Optional[str] = Query(None, description="Filter by MIME type"),
    has_checksum: Optional[bool] = Query(None, description="Filter files with/without checksum"),
    tag_ids: Optional[str] = Query(None, description="Filter by tag IDs (comma-separated)"),
    sort_by: str = Query("last_seen", description="Sort field (file_path, file_size, last_seen, storage_type, file_extension)"),
    sort_order: str = Query("desc", description="Sort order (asc/desc)"),
    db: Session = Depends(get_db)
):
    """
    List files in inventory with pagination, search, and filtering.

    Supports:
    - Pagination with page and page_size
    - Filtering by path, storage type, status, extension, MIME type, and tags
    - Search by filename/path
    - Sorting by multiple fields
    """
    # Parse tag IDs if provided
    tag_id_list = None
    if tag_ids:
        try:
            tag_id_list = [int(tid.strip()) for tid in tag_ids.split(',') if tid.strip()]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid tag_ids format. Must be comma-separated integers."
            )

    # Run database query in thread pool to avoid blocking the event loop
    result = await run_in_threadpool(
        _query_files_inventory_paginated,
        db, page, page_size, path_id, storage_type, status, search,
        extension, mime_type, has_checksum, tag_id_list, sort_by, sort_order
    )
    return result


def _get_storage_location_for_file(file_path: str, monitored_path: MonitoredPath) -> Optional[dict]:
    """Determine the cold storage location for a file based on its path."""
    if not monitored_path or not monitored_path.storage_locations:
        return None

    for loc in monitored_path.storage_locations:
        if file_path.startswith(loc.path):
            return {
                "id": loc.id,
                "name": loc.name,
                "path": loc.path
            }
    return None


def _query_files_inventory_paginated(
    db: Session,
    page: int,
    page_size: int,
    path_id: Optional[int],
    storage_type: Optional[StorageTypeSchema],
    status: Optional[str],
    search: Optional[str],
    extension: Optional[str],
    mime_type: Optional[str],
    has_checksum: Optional[bool],
    tag_ids: Optional[List[int]],
    sort_by: str,
    sort_order: str
) -> dict:
    """Query files inventory with pagination (runs in thread pool)."""
    from app.models import FileTag, Tag

    # Build base query
    query = db.query(FileInventory)

    # Apply filters
    if path_id:
        query = query.filter(FileInventory.path_id == path_id)

    if storage_type:
        query = query.filter(FileInventory.storage_type == storage_type)

    if status:
        query = query.filter(FileInventory.status == status)
    else:
        # Show active and migrating files by default
        query = query.filter(FileInventory.status.in_([FileStatus.ACTIVE, FileStatus.MIGRATING]))

    # Search filter (case-insensitive partial match on file_path)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(FileInventory.file_path.ilike(search_pattern))

    # Extension filter
    if extension:
        # Ensure extension starts with dot
        ext = extension if extension.startswith('.') else f'.{extension}'
        query = query.filter(FileInventory.file_extension == ext.lower())

    # MIME type filter
    if mime_type:
        query = query.filter(FileInventory.mime_type.ilike(f"%{mime_type}%"))

    # Checksum filter
    if has_checksum is not None:
        if has_checksum:
            query = query.filter(FileInventory.checksum.isnot(None))
        else:
            query = query.filter(FileInventory.checksum.is_(None))

    # Tag filter
    if tag_ids:
        # Files that have ANY of the specified tags
        query = query.join(FileInventory.tags).filter(
            FileTag.tag_id.in_(tag_ids)
        ).distinct()

    # Get total count before pagination
    total_count = query.count()

    # Apply sorting
    valid_sort_fields = {
        "file_path": FileInventory.file_path,
        "file_size": FileInventory.file_size,
        "last_seen": FileInventory.last_seen,
        "storage_type": FileInventory.storage_type,
        "file_mtime": FileInventory.file_mtime,
        "file_atime": FileInventory.file_atime,
        "file_extension": FileInventory.file_extension
    }

    sort_field = valid_sort_fields.get(sort_by, FileInventory.last_seen)

    if sort_order.lower() == "asc":
        query = query.order_by(sort_field.asc())
    else:
        query = query.order_by(sort_field.desc())

    # Calculate pagination
    skip = (page - 1) * page_size
    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1

    # Execute query with pagination
    files = query.offset(skip).limit(page_size).all()

    # Get all monitored paths for the files in this result set
    path_ids = set(f.path_id for f in files)
    paths_map = {}
    if path_ids:
        paths = db.query(MonitoredPath).filter(MonitoredPath.id.in_(path_ids)).all()
        paths_map = {p.id: p for p in paths}

    # Convert to dicts and add storage location info for cold storage files
    items = []
    for file in files:
        file_dict = {
            "id": file.id,
            "path_id": file.path_id,
            "file_path": file.file_path,
            "storage_type": file.storage_type.value if hasattr(file.storage_type, 'value') else file.storage_type,
            "file_size": file.file_size,
            "file_mtime": file.file_mtime.isoformat() if file.file_mtime else None,
            "file_atime": file.file_atime.isoformat() if file.file_atime else None,
            "file_ctime": file.file_ctime.isoformat() if file.file_ctime else None,
            "checksum": file.checksum,
            "file_extension": file.file_extension,
            "mime_type": file.mime_type,
            "status": file.status.value if hasattr(file.status, 'value') else file.status,
            "last_seen": file.last_seen.isoformat() if file.last_seen else None,
            "created_at": file.created_at.isoformat() if file.created_at else None,
            "tags": [
                {
                    "id": ft.id,
                    "file_id": ft.file_id,
                    "tag": {
                        "id": ft.tag.id,
                        "name": ft.tag.name,
                        "description": ft.tag.description,
                        "color": ft.tag.color,
                        "created_at": ft.tag.created_at.isoformat() if ft.tag.created_at else None
                    },
                    "tagged_at": ft.tagged_at.isoformat() if ft.tagged_at else None,
                    "tagged_by": ft.tagged_by
                }
                for ft in file.tags
            ],
            "storage_location": None
        }

        # Add storage location info for cold storage files
        if file.storage_type == StorageType.COLD:
            monitored_path = paths_map.get(file.path_id)
            storage_loc = _get_storage_location_for_file(file.file_path, monitored_path)
            file_dict["storage_location"] = storage_loc

        items.append(file_dict)

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }


@router.post("/move", status_code=status.HTTP_202_ACCEPTED)
async def move_file(request: FileMoveRequest, db: Session = Depends(get_db)):
    """Move a file on-demand."""
    # Run file operation in thread pool to avoid blocking the event loop
    result = await run_in_threadpool(_move_file_operation, request, db)
    return result


def _move_file_operation(request: FileMoveRequest, db: Session) -> dict:
    """Move file operation (runs in thread pool)."""
    source = Path(request.source_path)
    destination = Path(request.destination_path)

    if not source.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file does not exist: {request.source_path}"
        )

    # Ensure destination directory exists
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Move the file
    success, error = FileMover.move_file(source, destination, request.operation_type)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to move file: {error}"
        )

    # Record the move (optional - might not be associated with a monitored path)
    try:
        file_size = destination.stat().st_size if destination.exists() else source.stat().st_size

        # Check if a record already exists to prevent duplicates
        existing_record = db.query(FileRecord).filter(
            (FileRecord.original_path == str(source)) |
            (FileRecord.cold_storage_path == str(destination))
        ).first()

        if existing_record:
            # Update existing record
            existing_record.cold_storage_path = str(destination)
            existing_record.file_size = file_size
            existing_record.operation_type = request.operation_type
        else:
            # Create new record
            file_record = FileRecord(
                path_id=None,  # Manual move, not associated with a path
                original_path=str(source),
                cold_storage_path=str(destination),
                file_size=file_size,
                operation_type=request.operation_type,
                criteria_matched=None
            )
            db.add(file_record)
        db.commit()
    except Exception:
        # If recording fails, that's okay - the file was moved successfully
        db.rollback()
        pass

    return {"message": "File moved successfully", "destination": str(destination)}


@router.get("/browse")
async def browse_files(
    directory: str,
    storage_type: Optional[str] = "hot"  # "hot" or "cold"
):
    """Browse files in a directory."""
    # Run file system operations in thread pool to avoid blocking the event loop
    result = await run_in_threadpool(_browse_directory, directory, storage_type)
    return result


def _browse_directory(directory: str, storage_type: str) -> dict:
    """Browse directory (runs in thread pool)."""
    try:
        dir_path = Path(directory)

        # Security: prevent directory traversal
        # Resolve the path to prevent .. attacks
        try:
            # Check for directory traversal attempts in the input
            if ".." in str(dir_path) or "//" in str(dir_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid directory path: directory traversal not allowed"
                )
            # Resolve to get absolute path
            resolved_path = dir_path.resolve()
            # Ensure it's actually a directory
            if not resolved_path.is_dir():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path is not a directory"
                )
            dir_path = resolved_path
        except (OSError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid directory path: {str(e)}"
            )

        if not dir_path.exists() or not dir_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Directory does not exist: {directory}"
            )

        files = []
        dirs = []

        for item in dir_path.iterdir():
            try:
                stat_info = item.stat()
                item_info = {
                    "name": item.name,
                    "path": str(item),
                    "size": stat_info.st_size if item.is_file() else 0,
                    "is_file": item.is_file(),
                    "is_dir": item.is_dir(),
                    "modified": stat_info.st_mtime
                }

                if item.is_file():
                    files.append(item_info)
                else:
                    dirs.append(item_info)
            except (OSError, PermissionError):
                continue

        return {
            "directory": directory,
            "storage_type": storage_type,
            "files": sorted(files, key=lambda x: x["name"]),
            "directories": sorted(dirs, key=lambda x: x["name"])
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error browsing directory: {str(e)}"
        )


@router.post("/thaw/{inventory_id}")
async def thaw_file(
    inventory_id: int,
    pin: bool = False,
    db: Session = Depends(get_db)
):
    """Thaw a file (move back from cold storage to hot storage)."""
    # Run thaw operation in thread pool to avoid blocking the event loop
    result = await run_in_threadpool(_thaw_file_operation, db, inventory_id, pin)
    return result


def _thaw_file_operation(db: Session, inventory_id: int, pin: bool) -> dict:
    """Thaw file operation (runs in thread pool)."""
    # Find the inventory entry
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.COLD
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage"
        )

    # Find the associated file record by matching paths
    file_record = db.query(FileRecord).filter(
        FileRecord.cold_storage_path == inventory_entry.file_path
    ).first()

    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No file record found for inventory entry {inventory_id}"
        )

    success, error = FileThawer.thaw_file(file_record, pin=pin, db=db)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error or "Failed to thaw file"
        )

    # Update the inventory entry status
    inventory_entry.status = "active"  # FileStatus.ACTIVE
    db.commit()

    return {
        "message": f"File thawed successfully{' and pinned' if pin else ''}",
        "inventory_id": inventory_id,
        "pinned": pin
    }


@router.post("/relocate/{inventory_id}", status_code=status.HTTP_202_ACCEPTED)
async def relocate_file(
    inventory_id: int,
    request: FileRelocateRequest,
    db: Session = Depends(get_db)
):
    """
    Start an async relocation of a file from one cold storage location to another.

    This endpoint creates a background task to move the file between cold storage
    locations. The operation runs asynchronously and returns a task ID that can be
    used to check progress.

    Returns 202 Accepted with a task_id that can be polled for status.
    """
    result = await run_in_threadpool(_create_relocate_task, db, inventory_id, request.target_storage_location_id)
    return result


def _create_relocate_task(db: Session, inventory_id: int, target_storage_location_id: int) -> dict:
    """Create a relocation task (runs in thread pool)."""
    from app.services.relocation_manager import relocation_manager

    # Find the inventory entry
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.COLD
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage"
        )

    # Get the monitored path
    monitored_path = db.query(MonitoredPath).filter(
        MonitoredPath.id == inventory_entry.path_id
    ).first()

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}"
        )

    # Get the target storage location
    target_location = db.query(ColdStorageLocation).filter(
        ColdStorageLocation.id == target_storage_location_id
    ).first()

    if not target_location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target storage location with id {target_storage_location_id} not found"
        )

    # Verify target location is associated with this path
    path_location_ids = [loc.id for loc in monitored_path.storage_locations]
    if target_storage_location_id not in path_location_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target storage location is not associated with this path. Valid locations: {path_location_ids}"
        )

    # Determine the current file location
    current_file_path = Path(inventory_entry.file_path)
    if not current_file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file does not exist: {inventory_entry.file_path}"
        )

    # Find current storage location by matching the file path prefix
    current_location = None
    for loc in monitored_path.storage_locations:
        if inventory_entry.file_path.startswith(loc.path):
            current_location = loc
            break

    if not current_location:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not determine current storage location for file"
        )

    if current_location.id == target_storage_location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is already in the target storage location"
        )

    # Get file size
    file_size = current_file_path.stat().st_size

    # Set status to MIGRATING immediately so UI reflects the pending operation
    inventory_entry.status = FileStatus.MIGRATING
    db.commit()
    logger = logging.getLogger(__name__)
    logger.info(f"Set file {inventory_id} status to MIGRATING (task pending)")

    # Create the relocation task
    try:
        task_id = relocation_manager.create_task(
            inventory_id=inventory_id,
            file_path=inventory_entry.file_path,
            file_size=file_size,
            source_location_id=current_location.id,
            source_location_name=current_location.name,
            target_location_id=target_location.id,
            target_location_name=target_location.name
        )
    except ValueError as e:
        # Reset status if task creation fails
        inventory_entry.status = FileStatus.ACTIVE
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )

    return {
        "message": "Relocation task created",
        "task_id": task_id,
        "inventory_id": inventory_id,
        "source_location": {
            "id": current_location.id,
            "name": current_location.name
        },
        "target_location": {
            "id": target_location.id,
            "name": target_location.name
        }
    }


@router.get("/relocate/{inventory_id}/options")
async def get_relocate_options(
    inventory_id: int,
    db: Session = Depends(get_db)
):
    """
    Get available cold storage locations for relocating a file.

    Returns the list of valid target storage locations for a file,
    excluding the current location.
    """
    result = await run_in_threadpool(_get_relocate_options, db, inventory_id)
    return result


def _get_relocate_options(db: Session, inventory_id: int) -> dict:
    """Get relocate options (runs in thread pool)."""
    # Find the inventory entry
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.COLD
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage"
        )

    # Get the monitored path
    monitored_path = db.query(MonitoredPath).filter(
        MonitoredPath.id == inventory_entry.path_id
    ).first()

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}"
        )

    # Find current storage location
    current_location_id = None
    for loc in monitored_path.storage_locations:
        if inventory_entry.file_path.startswith(loc.path):
            current_location_id = loc.id
            break

    # Get all available storage locations for this path, excluding current
    available_locations = [
        {
            "id": loc.id,
            "name": loc.name,
            "path": loc.path,
            "is_current": loc.id == current_location_id
        }
        for loc in monitored_path.storage_locations
    ]

    return {
        "inventory_id": inventory_id,
        "file_path": inventory_entry.file_path,
        "current_location_id": current_location_id,
        "available_locations": available_locations,
        "can_relocate": len([l for l in available_locations if not l["is_current"]]) > 0
    }


@router.get("/relocate/tasks")
async def get_relocation_tasks(
    active_only: bool = Query(False, description="Only return active (pending/running) tasks"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of tasks to return")
):
    """
    Get list of relocation tasks.

    Returns recent relocation tasks including their status and progress.
    """
    from app.services.relocation_manager import relocation_manager

    if active_only:
        tasks = relocation_manager.get_all_active_tasks()
    else:
        tasks = relocation_manager.get_recent_tasks(limit=limit)

    return {
        "tasks": tasks,
        "count": len(tasks)
    }


@router.get("/relocate/tasks/{task_id}")
async def get_relocation_task_status(task_id: str):
    """
    Get the status of a specific relocation task.

    Returns detailed progress information for the task.
    """
    from app.services.relocation_manager import relocation_manager

    task = relocation_manager.get_task(task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relocation task {task_id} not found"
        )

    return task


@router.get("/relocate/{inventory_id}/status")
async def get_file_relocation_status(inventory_id: int):
    """
    Get the relocation status for a specific file.

    Returns the active relocation task for a file if one exists.
    """
    from app.services.relocation_manager import relocation_manager

    task = relocation_manager.get_task_for_inventory(inventory_id)

    return {
        "inventory_id": inventory_id,
        "has_active_task": task is not None,
        "task": task
    }


@router.post("/metadata/backfill", status_code=status.HTTP_200_OK)
async def backfill_metadata(
    path_id: Optional[int] = None,
    batch_size: int = Query(100, ge=1, le=1000, description="Files to process per batch"),
    compute_checksum: bool = Query(True, description="Whether to compute file checksums"),
    db: Session = Depends(get_db)
):
    """
    Backfill metadata (extension, MIME type, checksum) for existing files.

    This endpoint processes files in batches for scalability.
    - If path_id is provided, only files from that path are processed
    - If path_id is None, all files in inventory are processed
    - Batch processing prevents memory issues with large file counts
    - Progress is logged and committed after each batch
    """
    result = await run_in_threadpool(
        _backfill_metadata_operation,
        db, path_id, batch_size, compute_checksum
    )
    return result


def _backfill_metadata_operation(
    db: Session,
    path_id: Optional[int],
    batch_size: int,
    compute_checksum: bool
) -> dict:
    """Backfill metadata operation (runs in thread pool)."""
    from app.services.metadata_backfill import MetadataBackfillService

    service = MetadataBackfillService(db)

    if path_id:
        result = service.backfill_path(
            path_id=path_id,
            batch_size=batch_size,
            compute_checksum=compute_checksum
        )
    else:
        result = service.backfill_all(
            batch_size=batch_size,
            compute_checksum=compute_checksum
        )

    return {
        "success": True,
        "message": "Metadata backfill completed",
        **result
    }

