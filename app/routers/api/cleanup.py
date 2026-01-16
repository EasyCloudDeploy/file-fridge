"""API routes for file cleanup."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.file_cleanup import FileCleanup

router = APIRouter(prefix="/api/v1/cleanup", tags=["cleanup"])


@router.post("")
def cleanup_missing_files(
    path_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Clean up FileRecord entries for files that no longer exist."""
    results = FileCleanup.cleanup_missing_files(db, path_id=path_id)
    return results


@router.post("/duplicates")
def cleanup_duplicates(
    path_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Clean up duplicate FileRecord entries."""
    results = FileCleanup.cleanup_duplicates(db, path_id=path_id)
    return results

