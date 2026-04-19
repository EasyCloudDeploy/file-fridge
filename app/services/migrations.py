"""Service functions for file migration queries."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileStatus,
    FileTransactionHistory,
    MonitoredPath,
    RelocationTask,
    RelocationTaskStatus,
    StorageType,
    TransactionType,
)
from app.schemas import FreezingFileSchema
from app.services.relocation_manager import relocation_manager
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

    current_operations = scan_progress_manager.get_all_current_operations()
    failed_operations = scan_progress_manager.get_all_failed_operations()

    # We need to get files currently migrating in the db
    migrating_files = (
        db.query(FileInventory).filter(FileInventory.status == FileStatus.MIGRATING).all()
    )

    # We also need to get files that failed during the current scan,
    # even if their DB status was rolled back to ACTIVE.
    # To do this, we extract their paths from failed_operations.
    failed_file_paths = {op.get("file_path") for op in failed_operations if op.get("file_path")}
    failed_inventory_files = []
    if failed_file_paths:
        failed_inventory_files = (
            db.query(FileInventory)
            .filter(FileInventory.file_path.in_(failed_file_paths))
            .filter(FileInventory.status != FileStatus.MIGRATING)  # Avoid duplicates
            .all()
        )

    all_files = migrating_files + failed_inventory_files

    # Filter to only freeze/thaw files (not covered by a RelocationTask)
    freeze_thaw_files = [f for f in all_files if f.id not in active_relocation_ids]

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
        (op["path_id"], op.get("file_path"), op["operation"]): op for op in current_operations
    }

    failed_lookup = {
        (op["path_id"], op.get("file_path"), op["operation"]): op for op in failed_operations
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

        error_message = None
        failed_op = failed_lookup.get(operation_key)
        if failed_op:
            error_message = failed_op.get("error_message")

        # The file might not be in 'operation_progress' anymore if it failed,
        # but it will be in 'failed_lookup'. We still return it since its status
        # is MIGRATING (until it's rolled back in db, or if it got stuck).
        # We'll use the progress info if available, otherwise default to 0.

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
                error_message=error_message,
            )
        )

    return result


def _coerce_iso_to_datetime(value: Any) -> datetime:
    """Convert ISO datetime strings to timezone-aware datetimes for sorting."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _serialize_freeze_thaw_transaction(
    tx: FileTransactionHistory, locations_by_id: Dict[int, ColdStorageLocation]
) -> Dict[str, Any]:
    """Convert audit trail freeze/thaw entries to migration-row payload shape."""
    if tx.transaction_type == TransactionType.FREEZE:
        source_name = "Hot Storage"
        target_location = (
            locations_by_id.get(tx.new_storage_location_id) if tx.new_storage_location_id else None
        )
        target_name = target_location.name if target_location else "Cold Storage"
        source_id = 0
        target_id = tx.new_storage_location_id or 0
    else:
        source_location = (
            locations_by_id.get(tx.old_storage_location_id) if tx.old_storage_location_id else None
        )
        source_name = source_location.name if source_location else "Cold Storage"
        target_name = "Hot Storage"
        source_id = tx.old_storage_location_id or 0
        target_id = 0

    file_size = tx.file_size or 0
    is_success = bool(tx.success)

    return {
        "task_id": f"tx-{tx.id}",
        "inventory_id": tx.file_id,
        "file_path": tx.old_path or tx.new_path or "",
        "source_location_id": source_id,
        "source_location_name": source_name,
        "target_location_id": target_id,
        "target_location_name": target_name,
        "status": "completed" if is_success else "failed",
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
        "started_at": None,
        "completed_at": tx.created_at.isoformat() if tx.created_at else None,
        "bytes_total": file_size,
        "bytes_transferred": file_size if is_success else 0,
        "error_message": tx.error_message,
        "new_file_path": tx.new_path,
        "destination_path": tx.new_path,
        "percent_complete": 100 if is_success else 0,
        "_sort_time": tx.created_at.isoformat() if tx.created_at else None,
    }


def get_recent_migrations(db: Session, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent migrations including relocations and freeze/thaw operations."""
    relocation_recent = relocation_manager.get_recent_tasks(limit, db)
    relocation_rows: List[Dict[str, Any]] = []
    for item in relocation_recent:
        row = dict(item)
        row["destination_path"] = row.get("new_file_path")
        row["_sort_time"] = (
            row.get("completed_at") or row.get("started_at") or row.get("created_at")
        )
        relocation_rows.append(row)

    freeze_thaw_txs = (
        db.query(FileTransactionHistory)
        .filter(
            FileTransactionHistory.transaction_type.in_(
                [TransactionType.FREEZE, TransactionType.THAW]
            )
        )
        .order_by(FileTransactionHistory.created_at.desc())
        .limit(limit)
        .all()
    )

    location_ids = set()
    for tx in freeze_thaw_txs:
        if tx.old_storage_location_id:
            location_ids.add(tx.old_storage_location_id)
        if tx.new_storage_location_id:
            location_ids.add(tx.new_storage_location_id)

    locations_by_id: Dict[int, ColdStorageLocation] = {}
    if location_ids:
        locations_by_id = {
            loc.id: loc
            for loc in db.query(ColdStorageLocation)
            .filter(ColdStorageLocation.id.in_(location_ids))
            .all()
        }

    tx_rows = [_serialize_freeze_thaw_transaction(tx, locations_by_id) for tx in freeze_thaw_txs]

    combined = relocation_rows + tx_rows
    combined.sort(key=lambda row: _coerce_iso_to_datetime(row.get("_sort_time")), reverse=True)
    trimmed = combined[:limit]
    for row in trimmed:
        row.pop("_sort_time", None)
    return trimmed
