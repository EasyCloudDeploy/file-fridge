"""API routes for path management."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path
from app.database import get_db
from app.models import MonitoredPath
from app.schemas import MonitoredPathCreate, MonitoredPathUpdate, MonitoredPath as MonitoredPathSchema
from app.services.scheduler import scheduler_service

router = APIRouter(prefix="/api/v1/paths", tags=["paths"])


@router.get("", response_model=List[MonitoredPathSchema])
def list_paths(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all monitored paths."""
    paths = db.query(MonitoredPath).offset(skip).limit(limit).all()
    return paths


@router.post("", response_model=MonitoredPathSchema, status_code=status.HTTP_201_CREATED)
def create_path(path: MonitoredPathCreate, db: Session = Depends(get_db)):
    """Create a new monitored path."""
    # Validate paths exist
    source = Path(path.source_path)
    if not source.exists() or not source.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source path does not exist or is not a directory: {path.source_path}"
        )
    
    dest = Path(path.cold_storage_path)
    if not dest.exists():
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot create cold storage path: {str(e)}"
            )
    
    # Check for duplicate name
    existing = db.query(MonitoredPath).filter(MonitoredPath.name == path.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path with name '{path.name}' already exists"
        )
    
    db_path = MonitoredPath(**path.model_dump())
    db.add(db_path)
    db.commit()
    db.refresh(db_path)
    
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
def update_path(path_id: int, path_update: MonitoredPathUpdate, db: Session = Depends(get_db)):
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
    
    if "cold_storage_path" in update_data:
        dest = Path(update_data["cold_storage_path"])
        if not dest.exists():
            try:
                dest.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot create cold storage path: {str(e)}"
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
    
    # Update fields
    for field, value in update_data.items():
        setattr(path, field, value)
    
    db.commit()
    db.refresh(path)
    
    # Update scheduler job
    scheduler_service.remove_path_job(path_id)
    if path.enabled:
        scheduler_service.add_path_job(path)
    
    return path


@router.delete("/{path_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_path(path_id: int, db: Session = Depends(get_db)):
    """Delete a monitored path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    # Remove from scheduler
    scheduler_service.remove_path_job(path_id)
    
    db.delete(path)
    db.commit()
    return None


@router.post("/{path_id}/scan", status_code=status.HTTP_202_ACCEPTED)
def trigger_scan(path_id: int, db: Session = Depends(get_db)):
    """Manually trigger a scan for a path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    # Trigger scan asynchronously
    scheduler_service.trigger_scan(path_id)
    
    return {"message": f"Scan triggered for path {path_id}"}

