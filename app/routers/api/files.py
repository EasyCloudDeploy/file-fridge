"""API routes for file management."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pathlib import Path
from app.database import get_db
from app.models import FileRecord, MonitoredPath
from app.schemas import FileRecord as FileRecordSchema, FileMoveRequest
from app.services.file_mover import FileMover
from app.models import OperationType

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("", response_model=List[FileRecordSchema])
def list_files(
    path_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """List file records."""
    query = db.query(FileRecord)
    
    if path_id:
        query = query.filter(FileRecord.path_id == path_id)
    
    files = query.order_by(FileRecord.moved_at.desc()).offset(skip).limit(limit).all()
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
        if ".." in directory or directory.startswith("/") and not directory.startswith("/tmp"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid directory path"
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

