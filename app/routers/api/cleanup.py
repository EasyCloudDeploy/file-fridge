# ruff: noqa: B008
"""API routes for file cleanup."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.file_cleanup import FileCleanup

router = APIRouter(prefix="/api/v1/cleanup", tags=["cleanup"])


@router.post("")
def cleanup_missing_files(path_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Clean up FileRecord entries for files that no longer exist."""
    return FileCleanup.cleanup_missing_files(db, path_id=path_id)


@router.post("/duplicates")
def cleanup_duplicates(path_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Clean up duplicate FileRecord entries."""
    return FileCleanup.cleanup_duplicates(db, path_id=path_id)


@router.post("/symlinks")
def cleanup_symlinks(path_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """
    Clean up symlink entries from FileInventory.

    Symlinks should not be tracked in the inventory. This endpoint removes any
    symlink entries that may have been added before this fix was implemented.

    Returns:
        dict with cleanup results including checked, removed, and errors counts
    """
    return FileCleanup.cleanup_symlink_inventory_entries(db, path_id=path_id)
