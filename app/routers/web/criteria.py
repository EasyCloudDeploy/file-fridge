"""Criteria management web routes."""
from fastapi import APIRouter, Depends, Request, Form, HTTPException, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates
from starlette.datastructures import FormData
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.models import Criteria, MonitoredPath, CriterionType, Operator
from app.schemas import CriteriaCreate, CriteriaUpdate

router = APIRouter()
# Templates directory relative to project root
templates = Jinja2Templates(directory="app/templates")


@router.get("/paths/{path_id}/criteria/new", response_class=HTMLResponse)
async def create_criteria_form(request: Request, path_id: int, db: Session = Depends(get_db)):
    """Show create criteria form."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    return templates.TemplateResponse("criteria/create.html", {
        "request": request,
        "active_page": "paths",
        "path": path,
        "criterion": None
    })


@router.post("/paths/{path_id}/criteria", response_class=HTMLResponse)
async def create_criteria(
    request: Request,
    path_id: int,
    criterion_type: str = Form(...),
    operator: str = Form(...),
    value: str = Form(...),
    enabled: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Create a new criterion."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path with id {path_id} not found"
        )
    
    # Convert string to enum
    try:
        crit_type = CriterionType(criterion_type.lower())
        op = Operator(operator)
    except ValueError as e:
        return templates.TemplateResponse("criteria/create.html", {
            "request": request,
            "active_page": "paths",
            "path": path,
            "criterion": None,
            "error": f"Invalid criterion type or operator: {str(e)}"
        })
    
    # Handle checkbox - if present, it's enabled
    is_enabled = enabled is not None
    
    db_criteria = Criteria(
        path_id=path_id,
        criterion_type=crit_type,
        operator=op,
        value=value,
        enabled=is_enabled
    )
    db.add(db_criteria)
    db.commit()
    db.refresh(db_criteria)
    
    return RedirectResponse(url=f"/paths/{path_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/criteria/{criteria_id}/edit", response_class=HTMLResponse)
async def edit_criteria_form(request: Request, criteria_id: int, db: Session = Depends(get_db)):
    """Show edit criteria form."""
    criterion = db.query(Criteria).filter(Criteria.id == criteria_id).first()
    if not criterion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Criteria with id {criteria_id} not found"
        )
    
    path = db.query(MonitoredPath).filter(MonitoredPath.id == criterion.path_id).first()
    
    return templates.TemplateResponse("criteria/create.html", {
        "request": request,
        "active_page": "paths",
        "path": path,
        "criterion": criterion,
        "editing": True
    })


@router.post("/criteria/{criteria_id}", response_class=HTMLResponse)
async def update_criteria(
    request: Request,
    criteria_id: int,
    criterion_type: str = Form(...),
    operator: str = Form(...),
    value: str = Form(...),
    enabled: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Update a criterion."""
    criterion = db.query(Criteria).filter(Criteria.id == criteria_id).first()
    if not criterion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Criteria with id {criteria_id} not found"
        )
    
    path = db.query(MonitoredPath).filter(MonitoredPath.id == criterion.path_id).first()
    
    # Convert string to enum
    try:
        crit_type = CriterionType(criterion_type.lower())
        op = Operator(operator)
    except ValueError as e:
        return templates.TemplateResponse("criteria/create.html", {
            "request": request,
            "active_page": "paths",
            "path": path,
            "criterion": criterion,
            "editing": True,
            "error": f"Invalid criterion type or operator: {str(e)}"
        })
    
    # Handle checkbox - if present, it's enabled
    is_enabled = enabled is not None
    
    criterion.criterion_type = crit_type
    criterion.operator = op
    criterion.value = value
    criterion.enabled = is_enabled
    
    db.commit()
    db.refresh(criterion)
    
    return RedirectResponse(url=f"/paths/{criterion.path_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/criteria/{criteria_id}/delete", response_class=HTMLResponse)
async def delete_criteria(request: Request, criteria_id: int, db: Session = Depends(get_db)):
    """Delete a criterion."""
    criterion = db.query(Criteria).filter(Criteria.id == criteria_id).first()
    if not criterion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Criteria with id {criteria_id} not found"
        )
    
    path_id = criterion.path_id
    db.delete(criterion)
    db.commit()
    
    return RedirectResponse(url=f"/paths/{path_id}", status_code=status.HTTP_303_SEE_OTHER)

