"""API routes for file management."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from typing import List, Optional
from pathlib import Path
from datetime import datetime
from starlette.concurrency import run_in_threadpool
from app.database import get_db
from app.models import FileRecord, MonitoredPath, FileInventory, StorageType, FileStatus
from app.schemas import FileInventory as FileInventorySchema, FileMoveRequest, StorageType as StorageTypeSchema, PaginatedFileInventory
from app.services.file_mover import FileMover
from app.services.file_thawer import FileThawer
from app.models import OperationType
import math

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("", response_model=PaginatedFileInventory)
async def list_files(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    path_id: Optional[int] = Query(None, description="Filter by monitored path ID"),
    storage_type: Optional[StorageTypeSchema] = Query(None, description="Filter by storage type (hot/cold)"),
    status: Optional[str] = Query(None, description="Filter by file status"),
    search: Optional[str] = Query(None, description="Search in file path"),
    sort_by: str = Query("last_seen", description="Sort field (file_path, file_size, last_seen, storage_type)"),
    sort_order: str = Query("desc", description="Sort order (asc/desc)"),
    db: Session = Depends(get_db)
):
    """
    List files in inventory with pagination, search, and filtering.

    Supports:
    - Pagination with page and page_size
    - Filtering by path, storage type, and status
    - Search by filename/path
    - Sorting by multiple fields
    """
    # Run database query in thread pool to avoid blocking the event loop
    result = await run_in_threadpool(
        _query_files_inventory_paginated,
        db, page, page_size, path_id, storage_type, status, search, sort_by, sort_order
    )
    return result


def _query_files_inventory_paginated(
    db: Session,
    page: int,
    page_size: int,
    path_id: Optional[int],
    storage_type: Optional[StorageTypeSchema],
    status: Optional[str],
    search: Optional[str],
    sort_by: str,
    sort_order: str
) -> PaginatedFileInventory:
    """Query files inventory with pagination (runs in thread pool)."""
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
        # Only show active files by default for better performance
        query = query.filter(FileInventory.status == FileStatus.ACTIVE)

    # Search filter (case-insensitive partial match on file_path)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(FileInventory.file_path.ilike(search_pattern))

    # Get total count before pagination
    total_count = query.count()

    # Apply sorting
    valid_sort_fields = {
        "file_path": FileInventory.file_path,
        "file_size": FileInventory.file_size,
        "last_seen": FileInventory.last_seen,
        "storage_type": FileInventory.storage_type,
        "file_mtime": FileInventory.file_mtime,
        "file_atime": FileInventory.file_atime
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

    return PaginatedFileInventory(
        items=files,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


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

