import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileStatus,
    RelocationTask,
    RelocationTaskStatus,
    StorageType,
)
from app.schemas import RelocationTaskOut
from app.services.relocation_manager import relocation_manager

router = APIRouter(prefix="/api/v1/migrations", tags=["migrations"])
logger = logging.getLogger(__name__)


@router.get("/active", response_model=List[RelocationTaskOut])
def get_active_migrations(db: Session = Depends(get_db)) -> List[Dict[str, Any]]: # NOSONAR
    """Get all active file migrations."""
    return relocation_manager.get_all_active_tasks(db)


@router.get("/recent", response_model=List[RelocationTaskOut])
def get_recent_migrations(limit: int = 20, db: Session = Depends(get_db)) -> List[Dict[str, Any]]: # NOSONAR
    """Get recent file migrations."""
    return relocation_manager.get_recent_tasks(limit, db)


@router.get("/freezing")
def get_freezing_files(db: Session = Depends(get_db)) -> List[Dict[str, Any]]: # NOSONAR
    """Get files currently being frozen or thawed (MIGRATING status without an active RelocationTask)."""
    # Get inventory IDs that already have an active RelocationTask
    active_relocation_ids = {
        row[0]
        for row in db.query(RelocationTask.inventory_id).filter(
            RelocationTask.status.in_([RelocationTaskStatus.PENDING, RelocationTaskStatus.RUNNING])
        ).all()
    }

    # Find files in MIGRATING status not covered by a RelocationTask
    migrating_files = (
        db.query(FileInventory)
        .filter(FileInventory.status == FileStatus.MIGRATING)
        .all()
    )

    result = []
    for f in migrating_files:
        if f.id in active_relocation_ids:
            continue

        # Determine operation type from storage type
        if f.storage_type == StorageType.HOT:
            operation_type = "freeze"
            source_label = "Hot Storage"
            target_label = "Cold Storage"
        else:
            operation_type = "thaw"
            cold_location = (
                db.query(ColdStorageLocation)
                .filter(ColdStorageLocation.id == f.cold_storage_location_id)
                .first()
            )
            source_label = cold_location.name if cold_location else "Cold Storage"
            target_label = "Hot Storage"

        result.append(
            {
                "inventory_id": f.id,
                "file_path": f.file_path,
                "operation_type": operation_type,
                "source_label": source_label,
                "target_label": target_label,
                "file_size": f.file_size,
            }
        )

    return result
