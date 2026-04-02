"""Service functions for file migration queries."""

import logging
from pathlib import Path
from typing import List

from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileStatus,
    MonitoredPath,
    RelocationTask,
    RelocationTaskStatus,
    StorageType,
)
from app.schemas import FreezingFileSchema
from app.services.scan_progress import scan_progress_manager

logger = logging.getLogger(__name__)


def get_freezing_files(db: Session) -> List[FreezingFileSchema]:
    """
    Return files currently being frozen or thawed.

    These are files with MIGRATING status that have no active RelocationTask
    (i.e., they are mid-freeze or mid-thaw, not mid-cold-to-cold relocation).
    """
    # IDs already tracked by an active RelocationTask
    active_relocation_ids = {
        row[0]
        for row in db.query(RelocationTask.inventory_id)
        .filter(
            RelocationTask.status.in_([RelocationTaskStatus.PENDING, RelocationTaskStatus.RUNNING])
        )
        .all()
    }

    migrating_files = (
        db.query(FileInventory).filter(FileInventory.status == FileStatus.MIGRATING).all()
    )

    # Filter to only freeze/thaw files (not covered by a RelocationTask)
    freeze_thaw_files = [f for f in migrating_files if f.id not in active_relocation_ids]

    path_ids = {f.path_id for f in freeze_thaw_files}
    monitored_paths: dict[int, MonitoredPath] = {}
    if path_ids:
        monitored_paths = {
            path.id: path
            for path in db.query(MonitoredPath).filter(MonitoredPath.id.in_(path_ids)).all()
        }

    # Resolve cold storage location names in a single query to avoid N+1
    cold_ids = {
        f.cold_storage_location_id
        for f in freeze_thaw_files
        if f.storage_type != StorageType.HOT and f.cold_storage_location_id is not None
    }
    cold_locations: dict[int, ColdStorageLocation] = {}
    if cold_ids:
        cold_locations = {
            loc.id: loc
            for loc in db.query(ColdStorageLocation)
            .filter(ColdStorageLocation.id.in_(cold_ids))
            .all()
        }

    result = []
    operation_lookup = {
        (op["path_id"], op.get("file_path"), op["operation"]): op
        for op in scan_progress_manager.get_all_current_operations()
    }

    for f in freeze_thaw_files:
        monitored_path = monitored_paths.get(f.path_id)
        if f.storage_type == StorageType.HOT:
            operation_type = "freeze"
            operation_key = (f.path_id, f.file_path, "move_to_cold")
            source_label = "Hot Storage"
            target_label = "Cold Storage"
        else:
            operation_type = "thaw"
            operation_key = (f.path_id, f.file_path, "move_to_hot")
            cold_location = cold_locations.get(f.cold_storage_location_id)
            source_label = cold_location.name if cold_location else "Cold Storage"
            target_label = "Hot Storage"

        operation_progress = operation_lookup.get(operation_key, {})
        destination_path = operation_progress.get("destination_path")

        if destination_path is None and f.storage_type == StorageType.COLD and monitored_path:
            cold_location = cold_locations.get(f.cold_storage_location_id)
            if cold_location:
                try:
                    relative_path = Path(f.file_path).relative_to(Path(cold_location.path))
                    destination_path = str(Path(monitored_path.source_path) / relative_path)
                except Exception:
                    destination_path = None

        result.append(
            FreezingFileSchema(
                inventory_id=f.id,
                file_path=f.file_path,
                destination_path=destination_path,
                operation_type=operation_type,
                source_label=source_label,
                target_label=target_label,
                file_size=f.file_size,
                transferred_bytes=operation_progress.get("bytes_transferred", 0),
                total_bytes=operation_progress.get("bytes_total", f.file_size),
                transfer_rate_bytes_per_sec=operation_progress.get("current_speed", 0),
                eta_seconds=operation_progress.get("eta"),
                percent_complete=operation_progress.get("percent", 0),
            )
        )

    return result
