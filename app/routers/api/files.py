# ruff: noqa: B008
"""API routes for file management."""

import base64
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    PinnedFile,
    StorageType,
)
from app.schemas import (
    BulkActionResponse,
    BulkActionResult,
    BulkFileActionRequest,
    BulkFreezeRequest,
    FileMoveRequest,
    FileRelocateRequest,
)
from app.schemas import (
    StorageType as StorageTypeSchema,
)
from app.services.file_freezer import FileFreezer
from app.services.file_mover import FileMover
from app.services.file_thawer import FileThawer

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
                "available": storage_available,
            }
    return None


def _serialize_file(
    file: FileInventory, paths_map: dict, pinned_paths_set: Optional[set] = None
) -> dict:
    """Serialize a FileInventory object to a dictionary for JSON output."""
    file_dict = {
        "id": file.id,
        "path_id": file.path_id,
        "file_path": file.file_path,
        "storage_type": (
            file.storage_type.value if hasattr(file.storage_type, "value") else file.storage_type
        ),
        "file_size": file.file_size,
        "file_mtime": file.file_mtime.isoformat() if file.file_mtime else None,
        "file_atime": file.file_atime.isoformat() if file.file_atime else None,
        "file_ctime": file.file_ctime.isoformat() if file.file_ctime else None,
        "checksum": file.checksum,
        "file_extension": file.file_extension,
        "mime_type": file.mime_type,
        "status": file.status.value if hasattr(file.status, "value") else file.status,
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
                    "created_at": ft.tag.created_at.isoformat() if ft.tag.created_at else None,
                },
                "tagged_at": ft.tagged_at.isoformat() if ft.tagged_at else None,
                "tagged_by": ft.tagged_by,
            }
            for ft in file.tags
        ],
        "storage_location": None,
        "is_pinned": file.file_path in pinned_paths_set if pinned_paths_set else False,
    }

    if file.storage_type == StorageType.COLD:
        monitored_path = paths_map.get(file.path_id)
        storage_loc = _get_storage_location_for_file(file.file_path, monitored_path)
        file_dict["storage_location"] = storage_loc

    return file_dict


@router.get("")
def list_files(
    path_id: Optional[int] = Query(None, description="Filter by monitored path ID"),
    storage_type: Optional[StorageTypeSchema] = Query(
        None, description="Filter by storage type (hot/cold)"
    ),
    file_status: Optional[str] = Query(None, alias="status", description="Filter by file status"),
    search: Optional[str] = Query(None, description="Search in file path"),
    extension: Optional[str] = Query(
        None, description="Filter by file extension (e.g., .pdf, .jpg)"
    ),
    mime_type: Optional[str] = Query(None, description="Filter by MIME type"),
    has_checksum: Optional[bool] = Query(None, description="Filter files with/without checksum"),
    tag_ids: Optional[str] = Query(None, description="Filter by tag IDs (comma-separated)"),
    is_pinned: Optional[bool] = Query(None, description="Filter by pinned status"),
    min_size: Optional[int] = Query(None, description="Minimum file size in bytes"),
    max_size: Optional[int] = Query(None, description="Maximum file size in bytes"),
    min_mtime: Optional[datetime] = Query(None, description="Minimum modification time"),
    max_mtime: Optional[datetime] = Query(None, description="Maximum modification time"),
    storage_location_id: Optional[int] = Query(
        None, description="Filter by cold storage location ID"
    ),
    sort_by: str = Query(
        "last_seen",
        description="Sort field (file_path, file_size, last_seen, storage_type, file_extension)",
    ),
    sort_order: str = Query("desc", description="Sort order (asc/desc)"),
    page_size: int = Query(
        100, ge=10, le=500, description="Number of items per page (for pagination)"
    ),
    cursor: Optional[str] = Query(None, description="Pagination cursor (base64 encoded)"),
    db: Session = Depends(get_db),
):
    """
    Stream files in inventory as NDJSON.

    Each line is a JSON object:
    - First line: {"type": "metadata", "total": N, "filters": {...}, "sort": {...}}
    - File lines: {"type": "file", "data": {...}}
    - Last line: {"type": "complete", "count": N, "duration_ms": N}
    - On error: {"type": "error", "message": "...", "partial_count": N}
    """
    # Validate query parameters
    if min_size is not None and min_size < 0:
        raise HTTPException(
            status_code=400, detail="min_size must be non-negative (>= 0)"
        )

    if max_size is not None and max_size < 0:
        raise HTTPException(
            status_code=400, detail="max_size must be non-negative (>= 0)"
        )

    if min_size is not None and max_size is not None and min_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"min_size ({min_size}) cannot be greater than max_size ({max_size})"
        )

    if min_mtime is not None and max_mtime is not None and min_mtime > max_mtime:
        raise HTTPException(
            status_code=400,
            detail=f"min_mtime ({min_mtime.isoformat()}) cannot be greater than max_mtime ({max_mtime.isoformat()})"
        )

    from app.models import FileTag

    def generate_ndjson() -> Generator[str, None, None]:
        start_time = time.time()
        count = 0

        try:
            # Parse tag IDs if provided
            tag_id_list = None
            if tag_ids:
                try:
                    tag_id_list = [int(tid.strip()) for tid in tag_ids.split(",") if tid.strip()]
                except ValueError:
                    yield json.dumps(
                        {
                            "type": "error",
                            "message": "Invalid tag_ids format. Must be comma-separated integers.",
                            "partial_count": 0,
                        }
                    ) + "\n"
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
                ext = extension if extension.startswith(".") else f".{extension}"
                query = query.filter(FileInventory.file_extension == ext.lower())

            if mime_type:
                query = query.filter(FileInventory.mime_type.ilike(f"%{mime_type}%"))

            if has_checksum is not None:
                if has_checksum:
                    query = query.filter(FileInventory.checksum.isnot(None))
                else:
                    query = query.filter(FileInventory.checksum.is_(None))

            if tag_id_list:
                query = (
                    query.join(FileInventory.tags)
                    .filter(FileTag.tag_id.in_(tag_id_list))
                    .distinct()
                )

            if is_pinned is not None:
                if is_pinned:
                    # Filter for files that are in the PinnedFile table
                    query = query.filter(FileInventory.file_path.in_(db.query(PinnedFile.file_path)))
                else:
                    # Filter for files that are NOT in the PinnedFile table
                    query = query.filter(
                        FileInventory.file_path.notin_(db.query(PinnedFile.file_path))
                    )

            if min_size is not None:
                query = query.filter(FileInventory.file_size >= min_size)
            if max_size is not None:
                query = query.filter(FileInventory.file_size <= max_size)

            if min_mtime is not None:
                query = query.filter(FileInventory.file_mtime >= min_mtime)
            if max_mtime is not None:
                query = query.filter(FileInventory.file_mtime <= max_mtime)

            if storage_location_id is not None:
                query = query.filter(FileInventory.cold_storage_location_id == storage_location_id)

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
                "file_extension": FileInventory.file_extension,
                "status": FileInventory.status,
            }

            sort_field = valid_sort_fields.get(sort_by, FileInventory.last_seen)
            is_descending = sort_order.lower() != "asc"

            # Decode cursor if provided for keyset pagination
            cursor_data = None
            if cursor:
                try:
                    cursor_json = base64.b64decode(cursor).decode("utf-8")
                    cursor_data = json.loads(cursor_json)
                except (ValueError, json.JSONDecodeError) as e:
                    yield json.dumps(
                        {
                            "type": "error",
                            "message": f"Invalid cursor format: {e}",
                            "partial_count": 0,
                        }
                    ) + "\n"
                    return

            # Apply cursor-based pagination (keyset pagination)
            if cursor_data:
                last_id = cursor_data.get("id")
                last_sort_value = cursor_data.get("sort_value")

                if last_id is not None and last_sort_value is not None:
                    # For nullable fields, handle None values
                    if is_descending:
                        # DESC: get rows where (sort_value < last) OR (sort_value == last AND id < last_id)
                        if last_sort_value is None:
                            # If last value was None, only filter by id
                            query = query.filter(
                                and_(sort_field.is_(None), FileInventory.id < last_id)
                            )
                        else:
                            query = query.filter(
                                or_(
                                    sort_field < last_sort_value,
                                    sort_field.is_(None),
                                    and_(sort_field == last_sort_value, FileInventory.id < last_id),
                                )
                            )
                    # ASC: get rows where (sort_value > last) OR (sort_value == last AND id > last_id)
                    elif last_sort_value is None:
                        # If last value was None, get non-null values or higher ids with null
                        query = query.filter(
                            or_(
                                sort_field.isnot(None),
                                and_(sort_field.is_(None), FileInventory.id > last_id),
                            )
                        )
                    else:
                        query = query.filter(
                            or_(
                                sort_field > last_sort_value,
                                and_(sort_field == last_sort_value, FileInventory.id > last_id),
                            )
                        )

            # Apply sorting with secondary sort by id for stable pagination
            if is_descending:
                query = query.order_by(sort_field.desc(), FileInventory.id.desc())
            else:
                query = query.order_by(sort_field.asc(), FileInventory.id.asc())

            # Limit to page_size + 1 to detect if there are more results
            query = query.limit(page_size + 1)

            # Pre-fetch all monitored paths to avoid N+1 queries
            all_paths = db.query(MonitoredPath).all()
            paths_map = {p.id: p for p in all_paths}

            # Pre-fetch all pinned file paths for efficient lookup
            pinned_files = db.query(PinnedFile.file_path).all()
            pinned_paths_set = {p.file_path for p in pinned_files}

            # Collect results to check for has_more and generate cursor
            files_list = list(query.all())
            has_more = len(files_list) > page_size
            if has_more:
                files_list = files_list[:page_size]  # Remove the extra item

            # Generate next cursor if there are more results
            next_cursor = None
            if has_more and files_list:
                last_file = files_list[-1]
                # Get the sort value for cursor
                sort_value_raw = (
                    getattr(last_file, sort_by, None)
                    if sort_by in valid_sort_fields
                    else last_file.last_seen
                )
                # Convert datetime/enum to string for JSON serialization
                if hasattr(sort_value_raw, "isoformat"):
                    sort_value = sort_value_raw.isoformat()
                elif hasattr(sort_value_raw, "value"):
                    sort_value = sort_value_raw.value
                else:
                    sort_value = sort_value_raw

                cursor_obj = {"id": last_file.id, "sort_value": sort_value}
                next_cursor = base64.b64encode(json.dumps(cursor_obj).encode("utf-8")).decode(
                    "utf-8"
                )

            # Send metadata first (with pagination info)
            metadata = {
                "type": "metadata",
                "total": total_count,
                "page_size": page_size,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "filters": {
                    "path_id": path_id,
                    "storage_type": storage_type.value if storage_type else None,
                    "status": file_status,
                    "search": search,
                    "extension": extension,
                    "mime_type": mime_type,
                    "has_checksum": has_checksum,
                    "tag_ids": tag_id_list,
                    "is_pinned": is_pinned,
                    "min_size": min_size,
                    "max_size": max_size,
                    "min_mtime": min_mtime.isoformat() if min_mtime else None,
                    "max_mtime": max_mtime.isoformat() if max_mtime else None,
                    "storage_location_id": storage_location_id,
                },
                "sort": {"by": sort_by, "order": sort_order},
            }
            yield json.dumps(metadata) + "\n"

            # Stream files from the collected list
            for file in files_list:
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
            yield json.dumps(
                {
                    "type": "complete",
                    "count": count,
                    "duration_ms": duration_ms,
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                }
            ) + "\n"

        except Exception as e:
            logger.exception("Error streaming files")
            yield json.dumps({"type": "error", "message": str(e), "partial_count": count}) + "\n"

    return StreamingResponse(
        generate_ndjson(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # Disable nginx buffering
    )


@router.post("/move", status_code=status.HTTP_202_ACCEPTED)
def move_file(request: FileMoveRequest, db: Session = Depends(get_db)):
    """Move a file on-demand."""
    source = Path(request.source_path)
    destination = Path(request.destination_path)

    if not source.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file does not exist: {request.source_path}",
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    success, error = FileMover.move_file(source, destination, request.operation_type)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to move file: {error}",
        )

    try:
        file_size = destination.stat().st_size if destination.exists() else source.stat().st_size

        existing_record = (
            db.query(FileRecord)
            .filter(
                (FileRecord.original_path == str(source))
                | (FileRecord.cold_storage_path == str(destination))
            )
            .first()
        )

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
                criteria_matched=None,
            )
            db.add(file_record)
        db.commit()
    except Exception:
        db.rollback()

    return {"message": "File moved successfully", "destination": str(destination)}


@router.get("/browse")
def browse_files(
    directory: str, storage_type: Optional[str] = "hot", db: Session = Depends(get_db)
):
    """Browse files in a directory. Restricted to configured paths."""
    try:
        try:
            # Resolve the path to handle any '..' or symlinks
            resolved_path = Path(directory).resolve()
        except (OSError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid directory path: {e!s}"
            )

        # SECURITY: Validate that the requested path is within a configured monitored path or storage location
        monitored_paths = db.query(MonitoredPath.source_path).all()
        storage_paths = db.query(ColdStorageLocation.path).all()

        allowed_bases = [Path(p[0]).resolve() for p in monitored_paths] + [
            Path(p[0]).resolve() for p in storage_paths
        ]

        is_allowed = False
        for base in allowed_bases:
            try:
                # Check if resolved_path is base or a subdirectory of base
                resolved_path.relative_to(base)
                is_allowed = True
                break
            except ValueError:
                continue

        if not is_allowed:
            logger.warning(f"Unauthorized directory browse attempt: {directory}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Directory is not within a configured monitored path or storage location",
            )

        if not resolved_path.exists() or not resolved_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Directory does not exist: {directory}",
            )

        dir_path = resolved_path
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
                    "modified": stat_info.st_mtime,
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
            "directories": sorted(dirs, key=lambda x: x["name"]),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error browsing directory: {e!s}",
        ) from e


@router.post("/thaw/{inventory_id}")
def thaw_file(inventory_id: int, pin: bool = False, db: Session = Depends(get_db)):
    """Thaw a file (move back from cold storage to hot storage)."""
    inventory_entry = (
        db.query(FileInventory)
        .filter(FileInventory.id == inventory_id, FileInventory.storage_type == StorageType.COLD)
        .first()
    )

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage",
        )

    file_record = (
        db.query(FileRecord)
        .filter(FileRecord.cold_storage_path == inventory_entry.file_path)
        .first()
    )

    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No file record found for inventory entry {inventory_id}",
        )

    success, error = FileThawer.thaw_file(file_record, pin=pin, db=db)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error or "Failed to thaw file"
        )

    inventory_entry.status = "active"
    db.commit()

    return {
        "message": f"File thawed successfully{' and pinned' if pin else ''}",
        "inventory_id": inventory_id,
        "pinned": pin,
    }


@router.get("/freeze/{inventory_id}/options")
def get_freeze_options(inventory_id: int, db: Session = Depends(get_db)):
    """Get available cold storage locations for freezing a file."""
    inventory_entry = (
        db.query(FileInventory)
        .filter(FileInventory.id == inventory_id, FileInventory.storage_type == StorageType.HOT)
        .first()
    )

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in hot storage",
        )

    monitored_path = (
        db.query(MonitoredPath).filter(MonitoredPath.id == inventory_entry.path_id).first()
    )

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}",
        )

    available_locations = [
        {"id": loc.id, "name": loc.name, "path": loc.path, "available": Path(loc.path).exists()}
        for loc in monitored_path.storage_locations
    ]

    return {
        "inventory_id": inventory_id,
        "file_path": inventory_entry.file_path,
        "available_locations": available_locations,
        "can_freeze": len(available_locations) > 0,
    }


@router.post("/freeze/{inventory_id}")
def freeze_file(
    inventory_id: int,
    storage_location_id: int = Query(..., description="Target cold storage location ID"),
    pin: bool = Query(False, description="Pin file after freezing"),
    db: Session = Depends(get_db),
):
    """
    Freeze a file (move from hot storage to cold storage).

    Moves the file to the specified cold storage location and optionally
    pins it to prevent automatic thawing.
    """
    # Get the file from inventory
    inventory_entry = (
        db.query(FileInventory)
        .filter(FileInventory.id == inventory_id, FileInventory.storage_type == StorageType.HOT)
        .first()
    )

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in hot storage",
        )

    # Get the monitored path
    monitored_path = (
        db.query(MonitoredPath).filter(MonitoredPath.id == inventory_entry.path_id).first()
    )

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}",
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
            detail=f"Storage location {storage_location_id} is not associated with this path. Valid locations: {valid_ids}",
        )

    # Check storage location is accessible
    if not Path(target_location.path).exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location {target_location.name} is not accessible: {target_location.path}",
        )

    # Freeze the file
    success, error, cold_path = FileFreezer.freeze_file(
        file=inventory_entry,
        monitored_path=monitored_path,
        storage_location=target_location,
        pin=pin,
        db=db,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error or "Failed to freeze file",
        )

    return {
        "message": f"File frozen successfully{' and pinned' if pin else ''}",
        "inventory_id": inventory_id,
        "cold_storage_path": cold_path,
        "storage_location": {"id": target_location.id, "name": target_location.name},
        "pinned": pin,
    }


@router.post("/relocate/{inventory_id}", status_code=status.HTTP_202_ACCEPTED)
def relocate_file(inventory_id: int, request: FileRelocateRequest, db: Session = Depends(get_db)):
    """
    Start an async relocation of a file from one cold storage location to another.

    Returns 202 Accepted with a task_id that can be polled for status.
    """
    from app.services.relocation_manager import relocation_manager

    inventory_entry = (
        db.query(FileInventory)
        .filter(FileInventory.id == inventory_id, FileInventory.storage_type == StorageType.COLD)
        .first()
    )

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage",
        )

    monitored_path = (
        db.query(MonitoredPath).filter(MonitoredPath.id == inventory_entry.path_id).first()
    )

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}",
        )

    target_location = (
        db.query(ColdStorageLocation)
        .filter(ColdStorageLocation.id == request.target_storage_location_id)
        .first()
    )

    if not target_location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target storage location with id {request.target_storage_location_id} not found",
        )

    path_location_ids = [loc.id for loc in monitored_path.storage_locations]
    if request.target_storage_location_id not in path_location_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target storage location is not associated with this path. Valid locations: {path_location_ids}",
        )

    current_file_path = Path(inventory_entry.file_path)
    if not current_file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file does not exist: {inventory_entry.file_path}",
        )

    current_location = None
    for loc in monitored_path.storage_locations:
        if inventory_entry.file_path.startswith(loc.path):
            current_location = loc
            break

    if not current_location:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not determine current storage location for file",
        )

    if current_location.id == request.target_storage_location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is already in the target storage location",
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
            target_location_name=target_location.name,
        )
    except ValueError as e:
        inventory_entry.status = FileStatus.ACTIVE
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    return {
        "message": "Relocation task created",
        "task_id": task_id,
        "inventory_id": inventory_id,
        "source_location": {"id": current_location.id, "name": current_location.name},
        "target_location": {"id": target_location.id, "name": target_location.name},
    }


@router.get("/relocate/{inventory_id}/options")
def get_relocate_options(inventory_id: int, db: Session = Depends(get_db)):
    """Get available cold storage locations for relocating a file."""
    inventory_entry = (
        db.query(FileInventory)
        .filter(FileInventory.id == inventory_id, FileInventory.storage_type == StorageType.COLD)
        .first()
    )

    if not inventory_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File inventory entry with id {inventory_id} not found in cold storage",
        )

    monitored_path = (
        db.query(MonitoredPath).filter(MonitoredPath.id == inventory_entry.path_id).first()
    )

    if not monitored_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored path not found for inventory entry {inventory_id}",
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
            "is_current": loc.id == current_location_id,
        }
        for loc in monitored_path.storage_locations
    ]

    return {
        "inventory_id": inventory_id,
        "file_path": inventory_entry.file_path,
        "current_location_id": current_location_id,
        "available_locations": available_locations,
        "can_relocate": len([loc for loc in available_locations if not loc["is_current"]]) > 0,
    }


@router.get("/relocate/tasks")
def get_relocation_tasks(
    active_only: bool = Query(False, description="Only return active (pending/running) tasks"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of tasks to return"),
):
    """Get list of relocation tasks."""
    from app.services.relocation_manager import relocation_manager

    if active_only:
        tasks = relocation_manager.get_all_active_tasks()
    else:
        tasks = relocation_manager.get_recent_tasks(limit=limit)

    return {"tasks": tasks, "count": len(tasks)}


@router.get("/relocate/tasks/{task_id}")
def get_relocation_task_status(task_id: str):
    """Get the status of a specific relocation task."""
    from app.services.relocation_manager import relocation_manager

    task = relocation_manager.get_task(task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Relocation task {task_id} not found"
        )

    return task


@router.get("/relocate/{inventory_id}/status")
def get_file_relocation_status(inventory_id: int):
    """Get the relocation status for a specific file."""
    from app.services.relocation_manager import relocation_manager

    task = relocation_manager.get_task_for_inventory(inventory_id)

    return {"inventory_id": inventory_id, "has_active_task": task is not None, "task": task}


@router.post("/metadata/backfill", status_code=status.HTTP_200_OK)
def backfill_metadata(
    path_id: Optional[int] = None,
    batch_size: int = Query(100, ge=1, le=1000, description="Files to process per batch"),
    compute_checksum: bool = Query(True, description="Whether to compute file checksums"),
    db: Session = Depends(get_db),
):
    """Backfill metadata (extension, MIME type, checksum) for existing files."""
    from app.services.metadata_backfill import MetadataBackfillService

    service = MetadataBackfillService(db)

    if path_id:
        result = service.backfill_path(
            path_id=path_id, batch_size=batch_size, compute_checksum=compute_checksum
        )
    else:
        result = service.backfill_all(batch_size=batch_size, compute_checksum=compute_checksum)

    return {"success": True, "message": "Metadata backfill completed", **result}


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
            detail=f"File with inventory id {inventory_id} not found",
        )

    # Check if already pinned
    existing_pin = db.query(PinnedFile).filter(PinnedFile.file_path == file.file_path).first()

    if existing_pin:
        return {
            "message": "File is already pinned",
            "inventory_id": inventory_id,
            "file_path": file.file_path,
            "is_pinned": True,
        }

    # Create new pin
    pinned = PinnedFile(path_id=file.path_id, file_path=file.file_path)
    db.add(pinned)
    db.commit()

    logger.info(f"Pinned file: {file.file_path}")

    return {
        "message": "File pinned successfully",
        "inventory_id": inventory_id,
        "file_path": file.file_path,
        "is_pinned": True,
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
            detail=f"File with inventory id {inventory_id} not found",
        )

    # Find and delete the pin
    pin = db.query(PinnedFile).filter(PinnedFile.file_path == file.file_path).first()

    if not pin:
        return {
            "message": "File is not pinned",
            "inventory_id": inventory_id,
            "file_path": file.file_path,
            "is_pinned": False,
        }

    db.delete(pin)
    db.commit()

    logger.info(f"Unpinned file: {file.file_path}")

    return {
        "message": "File unpinned successfully",
        "inventory_id": inventory_id,
        "file_path": file.file_path,
        "is_pinned": False,
    }


# Bulk Operations Endpoints


@router.post("/bulk/thaw", response_model=BulkActionResponse)
def bulk_thaw_files(
    request: BulkFileActionRequest,
    pin: bool = Query(False, description="Pin files after thawing"),
    db: Session = Depends(get_db),
):
    """
    Bulk thaw multiple files from cold storage to hot storage.

    Only files that are currently in cold storage will be processed.
    """
    results = []
    successful = 0
    failed = 0

    for file_id in request.file_ids:
        try:
            # Get the file from inventory
            inventory_entry = (
                db.query(FileInventory)
                .filter(FileInventory.id == file_id, FileInventory.storage_type == StorageType.COLD)
                .first()
            )

            if not inventory_entry:
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=False, message="File not found in cold storage"
                    )
                )
                failed += 1
                continue

            # Get the file record
            file_record = (
                db.query(FileRecord)
                .filter(FileRecord.cold_storage_path == inventory_entry.file_path)
                .first()
            )

            if not file_record:
                results.append(
                    BulkActionResult(file_id=file_id, success=False, message="No file record found")
                )
                failed += 1
                continue

            # Thaw the file
            success, error = FileThawer.thaw_file(file_record, pin=pin, db=db)

            if success:
                inventory_entry.status = FileStatus.ACTIVE
                db.commit()
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=True, message="File thawed successfully"
                    )
                )
                successful += 1
            else:
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=False, message=error or "Failed to thaw file"
                    )
                )
                failed += 1

        except Exception as e:
            logger.exception(f"Error thawing file {file_id}")
            results.append(BulkActionResult(file_id=file_id, success=False, message=str(e)))
            failed += 1

    return BulkActionResponse(
        total=len(request.file_ids), successful=successful, failed=failed, results=results
    )


@router.post("/bulk/freeze", response_model=BulkActionResponse)
def bulk_freeze_files(request: BulkFreezeRequest, db: Session = Depends(get_db)):
    """
    Bulk freeze multiple files from hot storage to cold storage.

    All files must belong to monitored paths that have the specified
    storage location configured.
    """
    results = []
    successful = 0
    failed = 0

    # Validate storage location exists
    target_location = (
        db.query(ColdStorageLocation)
        .filter(ColdStorageLocation.id == request.storage_location_id)
        .first()
    )

    if not target_location:
        return BulkActionResponse(
            total=len(request.file_ids),
            successful=0,
            failed=len(request.file_ids),
            results=[
                BulkActionResult(
                    file_id=fid,
                    success=False,
                    message=f"Storage location {request.storage_location_id} not found",
                )
                for fid in request.file_ids
            ],
        )

    # Check storage location is accessible
    if not Path(target_location.path).exists():
        return BulkActionResponse(
            total=len(request.file_ids),
            successful=0,
            failed=len(request.file_ids),
            results=[
                BulkActionResult(
                    file_id=fid,
                    success=False,
                    message=f"Storage location {target_location.name} is not accessible",
                )
                for fid in request.file_ids
            ],
        )

    for file_id in request.file_ids:
        try:
            # Get the file from inventory
            inventory_entry = (
                db.query(FileInventory)
                .filter(FileInventory.id == file_id, FileInventory.storage_type == StorageType.HOT)
                .first()
            )

            if not inventory_entry:
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=False, message="File not found in hot storage"
                    )
                )
                failed += 1
                continue

            # Get the monitored path
            monitored_path = (
                db.query(MonitoredPath).filter(MonitoredPath.id == inventory_entry.path_id).first()
            )

            if not monitored_path:
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=False, message="Monitored path not found"
                    )
                )
                failed += 1
                continue

            # Validate storage location belongs to this path
            path_location_ids = [loc.id for loc in monitored_path.storage_locations]
            if request.storage_location_id not in path_location_ids:
                results.append(
                    BulkActionResult(
                        file_id=file_id,
                        success=False,
                        message="Storage location not associated with this path",
                    )
                )
                failed += 1
                continue

            # Freeze the file
            success, error, _cold_path = FileFreezer.freeze_file(
                file=inventory_entry,
                monitored_path=monitored_path,
                storage_location=target_location,
                pin=request.pin,
                db=db,
            )

            if success:
                results.append(
                    BulkActionResult(
                        file_id=file_id,
                        success=True,
                        message=f"File frozen to {target_location.name}",
                    )
                )
                successful += 1
            else:
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=False, message=error or "Failed to freeze file"
                    )
                )
                failed += 1

        except Exception as e:
            logger.exception(f"Error freezing file {file_id}")
            results.append(BulkActionResult(file_id=file_id, success=False, message=str(e)))
            failed += 1

    return BulkActionResponse(
        total=len(request.file_ids), successful=successful, failed=failed, results=results
    )


@router.post("/bulk/pin", response_model=BulkActionResponse)
def bulk_pin_files(request: BulkFileActionRequest, db: Session = Depends(get_db)):
    """
    Bulk pin multiple files to exclude them from automatic scan operations.
    """
    results = []
    successful = 0
    failed = 0

    try:
        # Get unique IDs to fetch
        unique_ids = list(set(request.file_ids))

        # Fetch all files
        files = db.query(FileInventory).filter(FileInventory.id.in_(unique_ids)).all()
        files_map = {f.id: f for f in files}

        # Find existing pins
        paths_previously_pinned = set()
        if files:
            file_paths = [f.file_path for f in files]
            existing_pins = db.query(PinnedFile).filter(PinnedFile.file_path.in_(file_paths)).all()
            paths_previously_pinned = {p.file_path for p in existing_pins}

        # Identify files to pin
        files_to_pin = []
        paths_just_pinned = set()

        for file in files:
            if file.file_path not in paths_previously_pinned:
                files_to_pin.append(file)
                paths_just_pinned.add(file.file_path)

        # Bulk insert new pins
        if files_to_pin:
            new_pins = [PinnedFile(path_id=f.path_id, file_path=f.file_path) for f in files_to_pin]
            db.bulk_save_objects(new_pins)
            db.commit()

        # Generate results (preserving order and handling duplicates)
        seen_paths = set()

        for file_id in request.file_ids:
            if file_id not in files_map:
                results.append(
                    BulkActionResult(file_id=file_id, success=False, message="File not found")
                )
                failed += 1
                continue

            file = files_map[file_id]
            file_path = file.file_path

            if file_path in paths_just_pinned:
                if file_path in seen_paths:
                    # It was pinned in this request, but this is a duplicate reference
                    results.append(
                        BulkActionResult(
                            file_id=file_id, success=True, message="File already pinned"
                        )
                    )
                else:
                    results.append(
                        BulkActionResult(
                            file_id=file_id, success=True, message="File pinned successfully"
                        )
                    )
            elif file_path in paths_previously_pinned:
                results.append(
                    BulkActionResult(file_id=file_id, success=True, message="File already pinned")
                )
            else:
                # Should not happen if logic is correct
                results.append(
                    BulkActionResult(file_id=file_id, success=False, message="Unknown error")
                )
                failed += 1
                continue

            successful += 1
            seen_paths.add(file_path)

    except Exception as e:
        db.rollback()
        logger.exception("Error in bulk pin operation")
        # If the bulk operation fails, we mark everything not yet processed as failed
        processed_count = len(results)
        for i in range(processed_count, len(request.file_ids)):
            file_id = request.file_ids[i]
            results.append(BulkActionResult(file_id=file_id, success=False, message=str(e)))
            failed += 1

    return BulkActionResponse(
        total=len(request.file_ids), successful=successful, failed=failed, results=results
    )


@router.post("/bulk/unpin", response_model=BulkActionResponse)
def bulk_unpin_files(request: BulkFileActionRequest, db: Session = Depends(get_db)):
    """
    Bulk unpin multiple files to allow automatic scan operations.
    """
    results = []
    successful = 0
    failed = 0

    try:
        # Get unique IDs to fetch
        unique_ids = list(set(request.file_ids))

        # Fetch all files
        files = db.query(FileInventory).filter(FileInventory.id.in_(unique_ids)).all()
        files_map = {f.id: f for f in files}

        if files:
            # Find existing pins
            file_paths = [f.file_path for f in files]
            existing_pins = db.query(PinnedFile).filter(PinnedFile.file_path.in_(file_paths)).all()
            paths_pinned = {p.file_path for p in existing_pins}

            # Bulk delete pins
            if existing_pins:
                pin_ids_to_delete = [p.id for p in existing_pins]
                db.query(PinnedFile).filter(PinnedFile.id.in_(pin_ids_to_delete)).delete(
                    synchronize_session=False
                )
                db.commit()
        else:
            paths_pinned = set()

        # Generate results
        for file_id in request.file_ids:
            if file_id not in files_map:
                results.append(
                    BulkActionResult(file_id=file_id, success=False, message="File not found")
                )
                failed += 1
                continue

            file = files_map[file_id]
            file_path = file.file_path

            if file_path in paths_pinned:
                results.append(
                    BulkActionResult(
                        file_id=file_id, success=True, message="File unpinned successfully"
                    )
                )
                # Remove from set to handle duplicates in request correctly (though typically idempotent)
                # If we have duplicate file_ids pointing to same path, first is "unpinned", second is "not pinned"
                # But wait, logic: "File unpinned successfully" vs "File not pinned".
                # If I delete it, it is gone. Next time I check, it is "not pinned".
                # To match loop behavior:
                # Loop: 1. Unpin (Success). 2. Unpin (Not pinned).
                # Here: We delete once.
                # So we should mark the first occurrence as "Unpinned", subsequent as "Not pinned".
                if file_path in paths_pinned:
                    paths_pinned.remove(file_path)
            else:
                results.append(
                    BulkActionResult(file_id=file_id, success=True, message="File not pinned")
                )

            successful += 1

    except Exception as e:
        db.rollback()
        logger.exception("Error in bulk unpin operation")
        processed_count = len(results)
        for i in range(processed_count, len(request.file_ids)):
            file_id = request.file_ids[i]
            results.append(BulkActionResult(file_id=file_id, success=False, message=str(e)))
            failed += 1

    return BulkActionResponse(
        total=len(request.file_ids), successful=successful, failed=failed, results=results
    )
