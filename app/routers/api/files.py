"""API routes for file management."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pathlib import Path
from app.database import get_db
from app.models import FileRecord, MonitoredPath, FileInventory, StorageType
from app.schemas import FileInventory as FileInventorySchema, FileMoveRequest, StorageType
from app.services.file_mover import FileMover
from app.services.file_thawer import FileThawer
from app.models import OperationType

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("", response_model=List[FileInventorySchema])
def list_files(
    path_id: Optional[int] = None,
    storage_type: Optional[StorageType] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """List files in inventory."""
    query = db.query(FileInventory)

    if path_id:
        query = query.filter(FileInventory.path_id == path_id)

    if storage_type:
        query = query.filter(FileInventory.storage_type == storage_type)

    files = query.order_by(FileInventory.last_seen.desc()).offset(skip).limit(limit).all()
    return files


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
def browse_files(
    directory: str,
    storage_type: Optional[str] = "hot"  # "hot" or "cold"
):
    """Browse files in a directory."""
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
def thaw_file(
    inventory_id: int,
    pin: bool = False,
    db: Session = Depends(get_db)
):
    """Thaw a file (move back from cold storage to hot storage)."""
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

