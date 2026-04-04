import logging
from typing import Annotated, Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import FreezingFileSchema, RelocationTaskOut
from app.services import migrations as migrations_service
from app.services.relocation_manager import relocation_manager

router = APIRouter(prefix="/api/v1/migrations", tags=["migrations"])
logger = logging.getLogger(__name__)


@router.get("/active", response_model=List[RelocationTaskOut])
def get_active_migrations(db: Annotated[Session, Depends(get_db)]) -> List[Dict[str, Any]]:  # NOSONAR
    """Get all active file migrations."""
    return relocation_manager.get_all_active_tasks(db)


@router.get("/recent", response_model=List[RelocationTaskOut])
def get_recent_migrations(
    limit: int = 20, db: Annotated[Session, Depends(get_db)]
) -> List[Dict[str, Any]]:  # NOSONAR
    """Get recent file migrations."""
    return relocation_manager.get_recent_tasks(limit, db)


@router.get("/freezing", response_model=List[FreezingFileSchema])
def get_freezing_files(db: Annotated[Session, Depends(get_db)]) -> List[FreezingFileSchema]:  # NOSONAR
    """Get files currently being frozen or thawed (MIGRATING status without an active RelocationTask)."""
    return migrations_service.get_freezing_files(db)
