"""Background relocation task management for moving files between cold storage locations."""

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    RelocationTask,
    RelocationTaskStatus,
)
from app.services.file_mover import FileMover
from app.database import SessionLocal

logger = logging.getLogger(__name__)


def serialize_relocation_task(task: RelocationTask) -> dict:
    """Convert a RelocationTask to a dictionary for JSON serialization."""
    percent_complete = 0
    if task.bytes_total > 0:
        percent_complete = min(100, int((task.bytes_transferred / task.bytes_total) * 100))
    elif task.status == RelocationTaskStatus.COMPLETED:
        percent_complete = 100

    return {
        "task_id": task.task_id,
        "inventory_id": task.inventory_id,
        "file_path": task.file_path,
        "source_location_id": task.source_location_id,
        "source_location_name": task.source_location_name,
        "target_location_id": task.target_location_id,
        "target_location_name": task.target_location_name,
        "status": task.status.value,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "bytes_total": task.bytes_total,
        "bytes_transferred": task.bytes_transferred,
        "error_message": task.error_message,
        "new_file_path": task.new_file_path,
        "percent_complete": percent_complete,
    }


class RelocationTaskManager:
    """
    Manager for tracking file relocation tasks using the database.

    Handles background file relocations between cold storage locations.
    Use the module-level `relocation_manager` instance.
    """

    def __init__(self):
        """Initialize the relocation task manager."""
        self._lock = threading.Lock()
        self._worker_thread = None
        self._shutdown = False
        self._cleanup_interval = 86400  # 1 day

        # Start recovery of interrupted tasks and background workers
        self._recover_tasks()
        self._start_worker_thread()
        self._start_cleanup_thread()

    def _recover_tasks(self):
        """Recover tasks that were running or pending before an application restart."""
        db = SessionLocal()
        try:
            # Find any tasks that were running, mark them as pending to be restarted
            running_tasks = db.query(RelocationTask).filter(RelocationTask.status == RelocationTaskStatus.RUNNING).all()
            for task in running_tasks:
                logger.info(f"Recovering interrupted relocation task {task.task_id}")
                task.status = RelocationTaskStatus.PENDING
                task.bytes_transferred = 0  # Reset progress since we'll restart from scratch
            if running_tasks:
                db.commit()
        except Exception as e:
            logger.error(f"Failed to recover tasks during startup: {e}")
        finally:
            db.close()

    def _start_worker_thread(self):
        """Start background thread to process relocation tasks."""

        def worker():
            while not self._shutdown:
                task_id = None
                db = SessionLocal()
                try:
                    # Look for pending tasks
                    task = db.query(RelocationTask).filter(
                        RelocationTask.status == RelocationTaskStatus.PENDING
                    ).order_by(RelocationTask.created_at.asc()).first()

                    if task:
                        task_id = task.task_id
                        # Claim the task
                        task.status = RelocationTaskStatus.RUNNING
                        task.started_at = datetime.now(tz=timezone.utc)
                        db.commit()
                    else:
                        task_id = None
                except Exception as e:
                    logger.error(f"Error querying pending relocation tasks: {e}")
                finally:
                    db.close()

                if task_id:
                    # Process the task in a new session
                    process_db = SessionLocal()
                    try:
                        self._process_task(task_id, process_db)
                    finally:
                        process_db.close()
                else:
                    # No tasks, sleep briefly
                    time.sleep(2)

        self._worker_thread = threading.Thread(target=worker, daemon=True, name="relocation-worker")
        self._worker_thread.start()
        logger.info("Relocation worker thread started")

    def _start_cleanup_thread(self):
        """Start background thread to cleanup old completed tasks."""

        def cleanup_worker():
            while not self._shutdown:
                try:
                    time.sleep(self._cleanup_interval)
                    self._cleanup_old_tasks()
                except Exception:
                    logger.exception("Error in relocation cleanup thread")

        cleanup_thread = threading.Thread(
            target=cleanup_worker, daemon=True, name="relocation-cleanup"
        )
        cleanup_thread.start()
        logger.info("Relocation cleanup thread started")

    def _cleanup_old_tasks(self):
        """Remove completed/failed tasks older than cleanup interval from database."""
        db = SessionLocal()
        try:
            from datetime import timedelta
            cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=1)
            # Find old tasks
            db.query(RelocationTask).filter(
                RelocationTask.status.in_([RelocationTaskStatus.COMPLETED, RelocationTaskStatus.FAILED]),
                RelocationTask.completed_at < cutoff_date
            ).delete()
            db.commit()
            logger.debug("Cleaned up old relocation tasks")
        except Exception as e:
            logger.error(f"Failed to clean up old tasks: {e}")
        finally:
            db.close()

    def _process_task(self, task_id: str, db: Session):
        """Process a single relocation task."""
        # Get task from DB
        task = db.query(RelocationTask).filter(RelocationTask.task_id == task_id).first()
        if not task:
            logger.error(f"Task {task_id} not found for processing")
            return

        try:
            # Get the inventory entry
            inventory_entry = (
                db.query(FileInventory).filter(FileInventory.id == task.inventory_id).first()
            )

            if not inventory_entry:
                msg = f"Inventory entry {task.inventory_id} not found"
                raise Exception(msg)

            # Ensure status is MIGRATING
            if inventory_entry.status != FileStatus.MIGRATING:
                inventory_entry.status = FileStatus.MIGRATING
                db.commit()
                logger.info(f"Set file {task.inventory_id} status to MIGRATING")

            # Get monitored path
            monitored_path = (
                db.query(MonitoredPath).filter(MonitoredPath.id == inventory_entry.path_id).first()
            )

            if not monitored_path:
                msg = f"Monitored path not found for inventory {task.inventory_id}"
                raise Exception(msg)

            # Get target storage location
            target_location = (
                db.query(ColdStorageLocation)
                .filter(ColdStorageLocation.id == task.target_location_id)
                .first()
            )

            if not target_location:
                msg = f"Target storage location {task.target_location_id} not found"
                raise Exception(msg)

            # Find current storage location
            current_location = None
            for loc in monitored_path.storage_locations:
                if inventory_entry.file_path.startswith(loc.path):
                    current_location = loc
                    break

            if not current_location:
                msg = "Could not determine current storage location"
                raise Exception(msg)

            # Calculate paths
            current_file_path = Path(inventory_entry.file_path)
            if not current_file_path.exists():
                msg = f"Source file does not exist: {inventory_entry.file_path}"
                raise Exception(msg)

            # Get file size for progress tracking
            file_size = current_file_path.stat().st_size
            task.bytes_total = file_size
            db.commit()

            # Calculate relative path
            try:
                relative_path = current_file_path.relative_to(current_location.path)
            except ValueError:
                relative_path = current_file_path.name

            new_file_path = Path(target_location.path) / relative_path

            logger.info(f"Relocating file from {current_file_path} to {new_file_path}")

            last_db_update = time.time()

            # Progress callback to update bytes transferred
            def progress_callback(bytes_transferred: int):
                nonlocal last_db_update
                # Update DB at most once per second to avoid spam
                now = time.time()
                if now - last_db_update > 1.0:
                    try:
                        # Use a separate session for progress updates to not interfere with main transaction
                        prog_db = SessionLocal()
                        prog_task = prog_db.query(RelocationTask).filter(RelocationTask.task_id == task_id).first()
                        if prog_task:
                            prog_task.bytes_transferred = bytes_transferred
                            prog_db.commit()
                        prog_db.close()
                        last_db_update = now
                    except Exception as e:
                        logger.warning(f"Failed to update progress for task {task_id}: {e}")

            # Perform the move
            success, error = FileMover.move_file(
                current_file_path,
                new_file_path,
                OperationType.MOVE,
                progress_callback=progress_callback,
            )

            if not success:
                msg = f"File move failed: {error}"
                raise Exception(msg)

            # Update the inventory entry
            old_path = inventory_entry.file_path
            inventory_entry.file_path = str(new_file_path)
            inventory_entry.status = FileStatus.ACTIVE  # Reset status after successful migration

            # Create a file record for the relocation
            file_record = FileRecord(
                path_id=monitored_path.id,
                original_path=old_path,
                cold_storage_path=str(new_file_path),
                file_size=file_size,
                operation_type=OperationType.MOVE,
                criteria_matched=None,
            )
            db.add(file_record)

            # Update any existing file records that point to the old location
            existing_record = (
                db.query(FileRecord).filter(FileRecord.cold_storage_path == old_path).first()
            )
            if existing_record:
                existing_record.cold_storage_path = str(new_file_path)

            # Mark task as completed
            task.status = RelocationTaskStatus.COMPLETED
            task.completed_at = datetime.now(tz=timezone.utc)
            task.new_file_path = str(new_file_path)
            task.bytes_transferred = file_size

            db.commit()

            logger.info(f"Relocation task {task_id} completed successfully")

        except Exception as e:
            logger.exception(f"Relocation task {task_id} failed")

            # Reset status back to ACTIVE on failure
            try:
                inventory_entry = (
                    db.query(FileInventory).filter(FileInventory.id == task.inventory_id).first()
                )
                if inventory_entry and inventory_entry.status == FileStatus.MIGRATING:
                    inventory_entry.status = FileStatus.ACTIVE
                    db.commit()
                    logger.info(
                        f"Reset file {task.inventory_id} status to ACTIVE after failed migration"
                    )
            except Exception as db_error:
                logger.exception(f"Failed to reset file status: {db_error}")

            task.status = RelocationTaskStatus.FAILED
            task.error_message = str(e)
            task.completed_at = datetime.now(tz=timezone.utc)
            db.commit()

    def create_task(
        self,
        inventory_id: int,
        file_path: str,
        file_size: int,
        source_location_id: int,
        source_location_name: str,
        target_location_id: int,
        target_location_name: str,
    ) -> str:
        """
        Create a new relocation task.
        """
        db = SessionLocal()
        try:
            # Check if there's already an active task for this file
            existing_task = db.query(RelocationTask).filter(
                RelocationTask.inventory_id == inventory_id,
                RelocationTask.status.in_([RelocationTaskStatus.PENDING, RelocationTaskStatus.RUNNING])
            ).first()

            if existing_task:
                msg = "A relocation task is already in progress for this file"
                raise ValueError(msg)

            task_id = str(uuid.uuid4())
            task = RelocationTask(
                task_id=task_id,
                inventory_id=inventory_id,
                file_path=file_path,
                source_location_id=source_location_id,
                source_location_name=source_location_name,
                target_location_id=target_location_id,
                target_location_name=target_location_name,
                status=RelocationTaskStatus.PENDING,
                bytes_total=file_size,
            )

            db.add(task)
            db.commit()

            logger.info(f"Created relocation task {task_id}: {file_path} -> {target_location_name}")
            return task_id
        finally:
            db.close()

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get task status by task ID."""
        db = SessionLocal()
        try:
            task = db.query(RelocationTask).filter(RelocationTask.task_id == task_id).first()
            if not task:
                return None
            return serialize_relocation_task(task)
        finally:
            db.close()

    def get_task_for_inventory(self, inventory_id: int) -> Optional[dict]:
        """Get active task for an inventory entry."""
        db = SessionLocal()
        try:
            task = db.query(RelocationTask).filter(
                RelocationTask.inventory_id == inventory_id,
                RelocationTask.status.in_([RelocationTaskStatus.PENDING, RelocationTaskStatus.RUNNING])
            ).first()
            if not task:
                return None
            return serialize_relocation_task(task)
        finally:
            db.close()

    def get_all_active_tasks(self, db: Session = None) -> List[dict]:
        """Get all active (pending or running) tasks."""
        _db = db or SessionLocal()
        try:
            tasks = _db.query(RelocationTask).filter(
                RelocationTask.status.in_([RelocationTaskStatus.PENDING, RelocationTaskStatus.RUNNING])
            ).all()
            return [serialize_relocation_task(task) for task in tasks]
        finally:
            if not db:
                _db.close()

    def get_recent_tasks(self, limit: int = 20, db: Session = None) -> List[dict]:
        """Get recent tasks (active and recently completed)."""
        _db = db or SessionLocal()
        try:
            tasks = _db.query(RelocationTask).order_by(RelocationTask.created_at.desc()).limit(limit).all()
            return [serialize_relocation_task(task) for task in tasks]
        finally:
            if not db:
                _db.close()


# Global singleton instance
relocation_manager = RelocationTaskManager()
