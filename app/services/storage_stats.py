"""Service for calculating storage statistics."""

from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import FileInventory, FileStatus, StorageType


def get_file_counts_by_storage(db: Session, path_id: Optional[int] = None) -> Dict[str, int]:
    """
    Get file counts by storage type, optionally filtered by a specific path.
    Only includes files in ACTIVE, MIGRATING, or MOVED statuses.
    """
    # Tracked file statuses
    valid_statuses = [FileStatus.ACTIVE, FileStatus.MIGRATING, FileStatus.MOVED]

    # Query for hot storage
    hot_query = db.query(func.count(FileInventory.id)).filter(
        FileInventory.storage_type == StorageType.HOT,
        FileInventory.status.in_(valid_statuses),
    )
    if path_id is not None:
        hot_query = hot_query.filter(FileInventory.path_id == path_id)
    hot_count = hot_query.scalar() or 0

    # Query for cold storage
    cold_query = db.query(func.count(FileInventory.id)).filter(
        FileInventory.storage_type == StorageType.COLD,
        FileInventory.status.in_(valid_statuses),
    )
    if path_id is not None:
        cold_query = cold_query.filter(FileInventory.path_id == path_id)
    cold_count = cold_query.scalar() or 0

    return {
        "hot_file_count": hot_count,
        "cold_file_count": cold_count,
    }
