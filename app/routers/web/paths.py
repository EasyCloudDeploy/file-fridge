"""Path management web routes."""
from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import MonitoredPath, OperationType
from app.schemas import MonitoredPathCreate, MonitoredPathUpdate
from app.services.scheduler import scheduler_service
from pathlib import Path

router = APIRouter()
# Templates directory relative to project root
templates = Jinja2Templates(directory="app/templates")


@router.get("/paths", response_class=HTMLResponse)
async def list_paths(request: Request, db: Session = Depends(get_db)):
    """List all monitored paths."""
    paths = db.query(MonitoredPath).all()
    return templates.TemplateResponse("paths/list.html", {
        "request": request,
        "active_page": "paths",
        "paths": paths
    })


@router.get("/paths/new", response_class=HTMLResponse)
async def create_path_form(request: Request):
    """Show create path form."""
    return templates.TemplateResponse("paths/create.html", {
        "request": request,
        "active_page": "paths",
        "path": None
    })


@router.post("/paths", response_class=HTMLResponse)
async def create_path(
    request: Request,
    name: str = Form(...),
    source_path: str = Form(...),
    cold_storage_path: str = Form(...),
    operation_type: str = Form(...),
    check_interval_seconds: int = Form(...),
    enabled: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Create a new monitored path."""
    # Convert operation_type string to enum
    try:
        op_type = OperationType(operation_type.lower())
    except ValueError:
        return templates.TemplateResponse("paths/create.html", {
            "request": request,
            "active_page": "paths",
            "path": None,
            "error": f"Invalid operation type: {operation_type}"
        })
    
    path_data = MonitoredPathCreate(
        name=name,
        source_path=source_path,
        cold_storage_path=cold_storage_path,
        operation_type=op_type,
        check_interval_seconds=check_interval_seconds,
        enabled=enabled
    )
    
    # Validate paths
    source = Path(path_data.source_path)
    if not source.exists() or not source.is_dir():
        return templates.TemplateResponse("paths/create.html", {
            "request": request,
            "active_page": "paths",
            "path": path_data,
            "error": f"Source path does not exist or is not a directory: {path_data.source_path}"
        })
    
    dest = Path(path_data.cold_storage_path)
    if not dest.exists():
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return templates.TemplateResponse("paths/create.html", {
                "request": request,
                "active_page": "paths",
                "path": path_data,
                "error": f"Cannot create cold storage path: {str(e)}"
            })
    
    # Check for duplicate name
    existing = db.query(MonitoredPath).filter(MonitoredPath.name == path_data.name).first()
    if existing:
        return templates.TemplateResponse("paths/create.html", {
            "request": request,
            "active_page": "paths",
            "path": path_data,
            "error": f"Path with name '{path_data.name}' already exists"
        })
    
    db_path = MonitoredPath(**path_data.model_dump())
    db.add(db_path)
    db.commit()
    db.refresh(db_path)
    
    # Add to scheduler
    if db_path.enabled:
        scheduler_service.add_path_job(db_path)
    
    return RedirectResponse(url="/paths", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/paths/{path_id}", response_class=HTMLResponse)
async def get_path(request: Request, path_id: int, db: Session = Depends(get_db)):
    """Get path details."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    return templates.TemplateResponse("paths/detail.html", {
        "request": request,
        "active_page": "paths",
        "path": path
    })


@router.get("/paths/{path_id}/edit", response_class=HTMLResponse)
async def edit_path_form(request: Request, path_id: int, db: Session = Depends(get_db)):
    """Show edit path form."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    return templates.TemplateResponse("paths/create.html", {
        "request": request,
        "active_page": "paths",
        "path": path,
        "editing": True
    })


@router.post("/paths/{path_id}", response_class=HTMLResponse)
async def update_path(
    request: Request,
    path_id: int,
    name: str = Form(...),
    source_path: str = Form(...),
    cold_storage_path: str = Form(...),
    operation_type: str = Form(...),
    check_interval_seconds: int = Form(...),
    enabled: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Update a monitored path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    # Validate paths
    source = Path(source_path)
    if not source.exists() or not source.is_dir():
        return templates.TemplateResponse("paths/create.html", {
            "request": request,
            "active_page": "paths",
            "path": path,
            "editing": True,
            "error": f"Source path does not exist or is not a directory: {source_path}"
        })
    
    dest = Path(cold_storage_path)
    if not dest.exists():
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return templates.TemplateResponse("paths/create.html", {
                "request": request,
                "active_page": "paths",
                "path": path,
                "editing": True,
                "error": f"Cannot create cold storage path: {str(e)}"
            })
    
    # Convert operation_type string to enum
    try:
        op_type = OperationType(operation_type.lower())
    except ValueError:
        return templates.TemplateResponse("paths/create.html", {
            "request": request,
            "active_page": "paths",
            "path": path,
            "editing": True,
            "error": f"Invalid operation type: {operation_type}"
        })
    
    # Update fields
    path.name = name
    path.source_path = source_path
    path.cold_storage_path = cold_storage_path
    path.operation_type = op_type
    path.check_interval_seconds = check_interval_seconds
    path.enabled = enabled
    
    db.commit()
    db.refresh(path)
    
    # Update scheduler job
    scheduler_service.remove_path_job(path_id)
    if path.enabled:
        scheduler_service.add_path_job(path)
    
    return RedirectResponse(url=f"/paths/{path_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paths/{path_id}/delete", response_class=HTMLResponse)
async def delete_path(request: Request, path_id: int, db: Session = Depends(get_db)):
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
    
    return RedirectResponse(url="/paths", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paths/{path_id}/scan", response_class=HTMLResponse)
async def trigger_scan(request: Request, path_id: int, db: Session = Depends(get_db)):
    """Trigger manual scan."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    scheduler_service.trigger_scan(path_id)
    
    return RedirectResponse(url=f"/paths/{path_id}", status_code=status.HTTP_303_SEE_OTHER)

