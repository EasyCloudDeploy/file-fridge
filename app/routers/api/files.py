"""API routes for file management."""
import json
import time
import logging
from typing import Optional, Generator

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pathlib import Path

from app.database import get_db
from app.models import FileRecord, MonitoredPath, FileInventory, StorageType, FileStatus, ColdStorageLocation, PinnedFile, OperationType
from app.schemas import FileMoveRequest, FileRelocateRequest, StorageType as StorageTypeSchema
from app.services.file_mover import FileMover
from app.services.file_thawer import FileThawer
from app.services.file_freezer import FileFreezer

router = APIRouter(prefix="/api/v1/files", tags=["files"])
logger = logging.getLogger(__name__)


def _get_storage_location_for_file(file_path: str, monitored_path: MonitoredPath) -> Optional[dict]:
    """Determine the cold storage location for a file based on its path."""
    if not monitored_path or not monitored_path.storage_locations:
        return None

    for loc in monitored_path.storage_locations:
        if file_path.startswith(loc.path):
            storage_available = Path(loc.path).exists()
            return {
                "id": loc.id,
                "name": loc.name,
                "path": loc.path,
                "available": storage_available
            }
    return None


def _serialize_file(file: FileInventory, paths_map: dict, pinned_paths_set: set = None) -> dict:
    """Serialize a FileInventory object to a dictionary for JSON output."""
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
        "storage_location": None,
        "is_pinned": file.file_path in pinned_paths_set if pinned_paths_set else False
    }

    if file.storage_type == StorageType.COLD:
        monitored_path = paths_map.get(file.path_id)
        storage_loc = _get_storage_location_for_file(file.file_path, monitored_path)
        file_dict["storage_location"] = storage_loc

    return file_dict


@router.get("")
def list_files(
    path_id: Optional[int] = Query(None, description="Filter by monitored path ID"),
    storage_type: Optional[StorageTypeSchema] = Query(None, description="Filter by storage type (hot/cold)"),
    file_status: Optional[str] = Query(None, alias="status", description="Filter by file status"),
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
    Stream files in inventory as NDJSON.

    Each line is a JSON object:
    - First line: {"type": "metadata", "total": N, "filters": {...}, "sort": {...}}
    - File lines: {"type": "file", "data": {...}}
    - Last line: {"type": "complete", "count": N, "duration_ms": N}
    - On error: {"type": "error", "message": "...", "partial_count": N}
    """
    from app.models import FileTag

    def generate_ndjson() -> Generator[str, None, None]:
        start_time = time.time()
        count = 0

        try:
            # Parse tag IDs if provided
            tag_id_list = None
            if tag_ids:
                try:
                    tag_id_list = [int(tid.strip()) for tid in tag_ids.split(',') if tid.strip()]
                except ValueError:
                    yield json.dumps({
                        "type": "error",
                        "message": "Invalid tag_ids format. Must be comma-separated integers.",
                        "partial_count": 0
                    }) + "\n"
                    return

            # Build base query
            query = db.query(FileInventory)

            # Apply filters
            if path_id:
                query = query.filter(FileInventory.path_id == path_id)

            if storage_type:
                query = query.filter(FileInventory.storage_type == storage_type)

            if file_status:
                query = query.filter(FileInventory.status == file_status)

            if search:
                search_pattern = f"%{search}%"
                query = query.filter(FileInventory.file_path.ilike(search_pattern))

            if extension:
                ext = extension if extension.startswith('.') else f'.{extension}'
                query = query.filter(FileInventory.file_extension == ext.lower())

            if mime_type:
                query = query.filter(FileInventory.mime_type.ilike(f"%{mime_type}%"))

            if has_checksum is not None:
                if has_checksum:
                    query = query.filter(FileInventory.checksum.isnot(None))
                else:
                    query = query.filter(FileInventory.checksum.is_(None))

            if tag_id_list:
                query = query.join(FileInventory.tags).filter(
                    FileTag.tag_id.in_(tag_id_list)
                ).distinct()

            # Get total count first
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

            # Send metadata first
            metadata = {
                "type": "metadata",
                "total": total_count,
                "filters": {
                    "path_id": path_id,
                    "storage_type": storage_type.value if storage_type else None,
                    "status": file_status,
                    "search": search,
                    "extension": extension,
                    "mime_type": mime_type,
                    "has_checksum": has_checksum,
                    "tag_ids": tag_id_list
                },
                "sort": {
                    "by": sort_by,
                    "order": sort_order
                }
            }
            yield json.dumps(metadata) + "\n"

            # Pre-fetch all monitored paths to avoid N+1 queries
            all_paths = db.query(MonitoredPath).all()
            paths_map = {p.id: p for p in all_paths}

            # Pre-fetch all pinned file paths for efficient lookup
            pinned_files = db.query(PinnedFile.file_path).all()
            pinned_paths_set = {p.file_path for p in pinned_files}

            # Stream files using yield_per for memory efficiency
            for file in query.yield_per(100):
                try:
                    file_dict = _serialize_file(file, paths_map, pinned_paths_set)
                    yield json.dumps({"type": "file", "data": file_dict}) + "\n"
                    count += 1
                except Exception as e:
                    # Log serialization error but continue streaming
                    logger.warning(f"Error serializing file {file.id}: {e}")
                    continue

            # Send completion message
            duration_ms = int((time.time() - start_time) * 1000)
            yield json.dumps({
                "type": "complete",
                "count": count,
                "duration_ms": duration_ms
            }) + "\n"

        except Exception as e:
            logger.exception("Error streaming files")
            yield json.dumps({
                "type": "error",
                "message": str(e),
                "partial_count": count
            }) + "\n"

    return StreamingResponse(
        generate_ndjson(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.post("/move", status_code=status.HTTP_202_ACCEPTED)
def move_file(request: FileMoveRequest, db: Session = Depends(get_db)):
    """Move a file on-demand."""
    source = Path(request.source_path)
    destination = Path(request.destination_path)

    if not source.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file does not exist: {request.source_path}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    success, error = FileMover.move_file(source, destination, request.operation_type)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to move file: {error}"
        )

    try:
        file_size = destination.stat().st_size if destination.exists() else source.stat().st_size

        existing_record = db.query(FileRecord).filter(
            (FileRecord.original_path == str(source)) |
            (FileRecord.cold_storage_path == str(destination))
        ).first()

        if existing_record:
            existing_record.cold_storage_path = str(destination)
            existing_record.file_size = file_size
            existing_record.operation_type = request.operation_type
        else:
            file_record = FileRecord(
                path_id=None,
                original_path=str(source),
                cold_storage_path=str(destination),
                file_size=file_size,
                operation_type=request.operation_type,
                criteria_matched=None
            )
            db.add(file_record)
        db.commit()
    except Exception:
        db.rollback()

    return {"message": "File moved successfully", "destination": str(destination)}


@router.get("/browse")
def browse_files(
    directory: str,
    storage_type: Optional[str] = "hot"
):
    """Browse files in a directory."""
    try:
        dir_path = Path(directory)

        if ".." in str(dir_path) or "//" in str(dir_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid directory path: directory traversal not allowed"
            )

        try:
            resolved_path = dir_path.resolve()
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error browsing directory: {str(e)}"
        )


@router.post("/thaw/{inventory_id}")
def thaw_file(
    inventory_id: int,
    pin: bool = False,
    db: Session = Depends(get_db)
):
    """Thaw a file (move back from cold storage to hot storage)."""
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.COLD
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage"
        )

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

    inventory_entry.status = "active"
    db.commit()

    return {
        "message": f"File thawed successfully{' and pinned' if pin else ''}",
        "inventory_id": inventory_id,
        "pinned": pin
    }


@router.get("/freeze/{inventory_id}/options")
def get_freeze_options(
    inventory_id: int,
    db: Session = Depends(get_db)
):
    """Get available cold storage locations for freezing a file."""
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.HOT
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in hot storage"
        )

    monitored_path = db.query(MonitoredPath).filter(
        MonitoredPath.id == inventory_entry.path_id
    ).first()

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}"
        )

    available_locations = [
        {
            "id": loc.id,
            "name": loc.name,
            "path": loc.path,
            "available": Path(loc.path).exists()
        }
        for loc in monitored_path.storage_locations
    ]

    return {
        "inventory_id": inventory_id,
        "file_path": inventory_entry.file_path,
        "available_locations": available_locations,
        "can_freeze": len(available_locations) > 0
    }


@router.post("/freeze/{inventory_id}")
def freeze_file(
    inventory_id: int,
    storage_location_id: int = Query(..., description="Target cold storage location ID"),
    pin: bool = Query(False, description="Pin file after freezing"),
    db: Session = Depends(get_db)
):
    """
    Freeze a file (move from hot storage to cold storage).

    Moves the file to the specified cold storage location and optionally
    pins it to prevent automatic thawing.
    """
    # Get the file from inventory
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.HOT
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in hot storage"
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

    # Validate storage location belongs to this path
    target_location = None
    for loc in monitored_path.storage_locations:
        if loc.id == storage_location_id:
            target_location = loc
            break

    if not target_location:
        valid_ids = [loc.id for loc in monitored_path.storage_locations]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location {storage_location_id} is not associated with this path. Valid locations: {valid_ids}"
        )

    # Check storage location is accessible
    if not Path(target_location.path).exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location {target_location.name} is not accessible: {target_location.path}"
        )

    # Freeze the file
    success, error, cold_path = FileFreezer.freeze_file(
        file=inventory_entry,
        monitored_path=monitored_path,
        storage_location=target_location,
        pin=pin,
        db=db
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error or "Failed to freeze file"
        )

    return {
        "message": f"File frozen successfully{' and pinned' if pin else ''}",
        "inventory_id": inventory_id,
        "cold_storage_path": cold_path,
        "storage_location": {
            "id": target_location.id,
            "name": target_location.name
        },
        "pinned": pin
    }


@router.post("/relocate/{inventory_id}", status_code=status.HTTP_202_ACCEPTED)
def relocate_file(
    inventory_id: int,
    request: FileRelocateRequest,
    db: Session = Depends(get_db)
):
    """
    Start an async relocation of a file from one cold storage location to another.

    Returns 202 Accepted with a task_id that can be polled for status.
    """
    from app.services.relocation_manager import relocation_manager

    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.COLD
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage"
        )

    monitored_path = db.query(MonitoredPath).filter(
        MonitoredPath.id == inventory_entry.path_id
    ).first()

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}"
        )

    target_location = db.query(ColdStorageLocation).filter(
        ColdStorageLocation.id == request.target_storage_location_id
    ).first()

    if not target_location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target storage location with id {request.target_storage_location_id} not found"
        )

    path_location_ids = [loc.id for loc in monitored_path.storage_locations]
    if request.target_storage_location_id not in path_location_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target storage location is not associated with this path. Valid locations: {path_location_ids}"
        )

    current_file_path = Path(inventory_entry.file_path)
    if not current_file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file does not exist: {inventory_entry.file_path}"
        )

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

    if current_location.id == request.target_storage_location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is already in the target storage location"
        )

    file_size = current_file_path.stat().st_size

    # Set status to MIGRATING immediately
    inventory_entry.status = FileStatus.MIGRATING
    db.commit()
    logger.info(f"Set file {inventory_id} status to MIGRATING (task pending)")

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
def get_relocate_options(
    inventory_id: int,
    db: Session = Depends(get_db)
):
    """Get available cold storage locations for relocating a file."""
    inventory_entry = db.query(FileInventory).filter(
        FileInventory.id == inventory_id,
        FileInventory.storage_type == StorageType.COLD
    ).first()

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage"
        )

    monitored_path = db.query(MonitoredPath).filter(
        MonitoredPath.id == inventory_entry.path_id
    ).first()

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}"
        )

    current_location_id = None
    for loc in monitored_path.storage_locations:
        if inventory_entry.file_path.startswith(loc.path):
            current_location_id = loc.id
            break

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
def get_relocation_tasks(
    active_only: bool = Query(False, description="Only return active (pending/running) tasks"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of tasks to return")
):
    """Get list of relocation tasks."""
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
def get_relocation_task_status(task_id: str):
    """Get the status of a specific relocation task."""
    from app.services.relocation_manager import relocation_manager

    task = relocation_manager.get_task(task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relocation task {task_id} not found"
        )

    return task


@router.get("/relocate/{inventory_id}/status")
def get_file_relocation_status(inventory_id: int):
    """Get the relocation status for a specific file."""
    from app.services.relocation_manager import relocation_manager

    task = relocation_manager.get_task_for_inventory(inventory_id)

    return {
        "inventory_id": inventory_id,
        "has_active_task": task is not None,
        "task": task
    }


@router.post("/metadata/backfill", status_code=status.HTTP_200_OK)
def backfill_metadata(
    path_id: Optional[int] = None,
    batch_size: int = Query(100, ge=1, le=1000, description="Files to process per batch"),
    compute_checksum: bool = Query(True, description="Whether to compute file checksums"),
    db: Session = Depends(get_db)
):
    """Backfill metadata (extension, MIME type, checksum) for existing files."""
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


@router.post("/{inventory_id}/pin", status_code=status.HTTP_200_OK)
def pin_file(inventory_id: int, db: Session = Depends(get_db)):
    """
    Pin a file to exclude it from automatic scan operations.

    Pinned files will not be moved to cold storage or thawed automatically
    during scheduled scans.
    """
    # Get the file from inventory
    file = db.query(FileInventory).filter(FileInventory.id == inventory_id).first()
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with inventory id {inventory_id} not found"
        )

    # Check if already pinned
    existing_pin = db.query(PinnedFile).filter(
        PinnedFile.file_path == file.file_path
    ).first()

    if existing_pin:
        return {
            "message": "File is already pinned",
            "inventory_id": inventory_id,
            "file_path": file.file_path,
            "is_pinned": True
        }

    # Create new pin
    pinned = PinnedFile(
        path_id=file.path_id,
        file_path=file.file_path
    )
    db.add(pinned)
    db.commit()

    logger.info(f"Pinned file: {file.file_path}")

    return {
        "message": "File pinned successfully",
        "inventory_id": inventory_id,
        "file_path": file.file_path,
        "is_pinned": True
    }


@router.delete("/{inventory_id}/pin", status_code=status.HTTP_200_OK)
def unpin_file(inventory_id: int, db: Session = Depends(get_db)):
    """
    Remove pin from a file, allowing automatic scan operations.

    The file will be subject to normal scan criteria again and may be
    moved to cold storage or thawed automatically.
    """
    # Get the file from inventory
    file = db.query(FileInventory).filter(FileInventory.id == inventory_id).first()
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with inventory id {inventory_id} not found"
        )

    # Find and delete the pin
    pin = db.query(PinnedFile).filter(
        PinnedFile.file_path == file.file_path
    ).first()

    if not pin:
        return {
            "message": "File is not pinned",
            "inventory_id": inventory_id,
            "file_path": file.file_path,
            "is_pinned": False
        }

    db.delete(pin)
    db.commit()

    logger.info(f"Unpinned file: {file.file_path}")

    return {
        "message": "File unpinned successfully",
        "inventory_id": inventory_id,
        "file_path": file.file_path,
        "is_pinned": False
    }
