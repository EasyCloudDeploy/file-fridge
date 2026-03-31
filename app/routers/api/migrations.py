import logging
from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.relocation_manager import relocation_manager

router = APIRouter(prefix="/api/v1/migrations", tags=["migrations"])
logger = logging.getLogger(__name__)


@router.get("/active", response_model=List[Dict[str, Any]])
def get_active_migrations(db: Session = Depends(get_db)):
    """Get all active file migrations."""
    return relocation_manager.get_all_active_tasks()


@router.get("/recent", response_model=List[Dict[str, Any]])
def get_recent_migrations(limit: int = 20, db: Session = Depends(get_db)):
    """Get recent file migrations."""
    return relocation_manager.get_recent_tasks(limit)
