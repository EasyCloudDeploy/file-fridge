"""Background relocation task management for moving files between cold storage locations."""
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass, field, asdict
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import FileInventory, FileRecord, MonitoredPath, ColdStorageLocation, StorageType, OperationType, FileStatus
from app.services.file_mover import FileMover

logger = logging.getLogger(__name__)


@dataclass
class RelocationTask:
    """Represents a file relocation task."""
    task_id: str
    inventory_id: int
    file_path: str
    source_location_id: int
    source_location_name: str
    target_location_id: int
    target_location_name: str
    status: str  # "pending", "running", "completed", "failed"
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    bytes_total: int = 0
    bytes_transferred: int = 0
    error_message: Optional[str] = None
    new_file_path: Optional[str] = None

    @property
    def percent_complete(self) -> int:
        """Calculate percentage complete."""
        if self.bytes_total == 0:
            return 0 if self.status == "pending" else 100
        return min(100, int((self.bytes_transferred / self.bytes_total) * 100))

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['percent_complete'] = self.percent_complete
        return data


class RelocationTaskManager:
    """
    Thread-safe singleton manager for tracking file relocation tasks.

    Handles background file relocations between cold storage locations.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern implementation."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize the relocation task manager."""
        if self._initialized:
            return

        self._initialized = True
        self._tasks: Dict[str, RelocationTask] = {}  # task_id -> RelocationTask
        self._tasks_by_inventory: Dict[int, str] = {}  # inventory_id -> task_id (for active tasks)
        self._worker_thread = None
        self._task_queue: List[str] = []  # Queue of task_ids to process
        self._shutdown = False
        self._cleanup_interval = 3600  # 1 hour - keep completed tasks for reference
        self._start_worker_thread()
        self._start_cleanup_thread()

    def _start_worker_thread(self):
        """Start background thread to process relocation tasks."""
        def worker():
            from app.database import SessionLocal

            while not self._shutdown:
                task_id = None
                try:
                    # Check for pending tasks
                    with self._lock:
                        if self._task_queue:
                            task_id = self._task_queue.pop(0)

                    if task_id:
                        # Process the task
                        db = SessionLocal()
                        try:
                            self._process_task(task_id, db)
                        finally:
                            db.close()
                    else:
                        # No tasks, sleep briefly
                        time.sleep(1)

                except Exception as e:
                    logger.error(f"Error in relocation worker thread: {e}")
                    if task_id:
                        with self._lock:
                            if task_id in self._tasks:
                                self._tasks[task_id].status = "failed"
                                self._tasks[task_id].error_message = str(e)
                                self._tasks[task_id].completed_at = datetime.now().isoformat()

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
                except Exception as e:
                    logger.error(f"Error in relocation cleanup thread: {e}")

        cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True, name="relocation-cleanup")
        cleanup_thread.start()
        logger.info("Relocation cleanup thread started")

    def _cleanup_old_tasks(self):
        """Remove completed/failed tasks older than cleanup interval."""
        with self._lock:
            current_time = time.time()
            to_remove = []

            for task_id, task in self._tasks.items():
                if task.status in ["completed", "failed"] and task.completed_at:
                    completed_time = datetime.fromisoformat(task.completed_at).timestamp()
                    if current_time - completed_time > self._cleanup_interval:
                        to_remove.append(task_id)

            for task_id in to_remove:
                task = self._tasks.pop(task_id, None)
                if task:
                    self._tasks_by_inventory.pop(task.inventory_id, None)
                    logger.debug(f"Cleaned up old relocation task: {task_id}")

    def _process_task(self, task_id: str, db: Session):
        """Process a single relocation task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = "running"
            task.started_at = datetime.now().isoformat()

        try:
            # Get the inventory entry
            inventory_entry = db.query(FileInventory).filter(
                FileInventory.id == task.inventory_id
            ).first()

            if not inventory_entry:
                raise Exception(f"Inventory entry {task.inventory_id} not found")

            # Status should already be MIGRATING (set when task was created)
            # But ensure it's set in case of restart or other edge cases
            if inventory_entry.status != FileStatus.MIGRATING:
                inventory_entry.status = FileStatus.MIGRATING
                db.commit()
                logger.info(f"Set file {task.inventory_id} status to MIGRATING")

            # Get monitored path
            monitored_path = db.query(MonitoredPath).filter(
                MonitoredPath.id == inventory_entry.path_id
            ).first()

            if not monitored_path:
                raise Exception(f"Monitored path not found for inventory {task.inventory_id}")

            # Get target storage location
            target_location = db.query(ColdStorageLocation).filter(
                ColdStorageLocation.id == task.target_location_id
            ).first()

            if not target_location:
                raise Exception(f"Target storage location {task.target_location_id} not found")

            # Find current storage location
            current_location = None
            for loc in monitored_path.storage_locations:
                if inventory_entry.file_path.startswith(loc.path):
                    current_location = loc
                    break

            if not current_location:
                raise Exception("Could not determine current storage location")

            # Calculate paths
            current_file_path = Path(inventory_entry.file_path)
            if not current_file_path.exists():
                raise Exception(f"Source file does not exist: {inventory_entry.file_path}")

            # Get file size for progress tracking
            file_size = current_file_path.stat().st_size
            with self._lock:
                task.bytes_total = file_size

            # Calculate relative path
            try:
                relative_path = current_file_path.relative_to(current_location.path)
            except ValueError:
                relative_path = current_file_path.name

            new_file_path = Path(target_location.path) / relative_path

            logger.info(f"Relocating file from {current_file_path} to {new_file_path}")

            # Progress callback to update bytes transferred
            def progress_callback(bytes_transferred: int):
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].bytes_transferred = bytes_transferred

            # Perform the move
            success, error = FileMover.move_file(
                current_file_path,
                new_file_path,
                OperationType.MOVE,
                progress_callback=progress_callback
            )

            if not success:
                raise Exception(f"File move failed: {error}")

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
                criteria_matched=None
            )
            db.add(file_record)

            # Update any existing file records that point to the old location
            existing_record = db.query(FileRecord).filter(
                FileRecord.cold_storage_path == old_path
            ).first()
            if existing_record:
                existing_record.cold_storage_path = str(new_file_path)

            db.commit()

            # Mark task as completed
            with self._lock:
                task.status = "completed"
                task.completed_at = datetime.now().isoformat()
                task.new_file_path = str(new_file_path)
                task.bytes_transferred = file_size
                self._tasks_by_inventory.pop(task.inventory_id, None)

            logger.info(f"Relocation task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"Relocation task {task_id} failed: {e}")

            # Reset status back to ACTIVE on failure
            try:
                inventory_entry = db.query(FileInventory).filter(
                    FileInventory.id == task.inventory_id
                ).first()
                if inventory_entry and inventory_entry.status == FileStatus.MIGRATING:
                    inventory_entry.status = FileStatus.ACTIVE
                    db.commit()
                    logger.info(f"Reset file {task.inventory_id} status to ACTIVE after failed migration")
            except Exception as db_error:
                logger.error(f"Failed to reset file status: {db_error}")

            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = "failed"
                    self._tasks[task_id].error_message = str(e)
                    self._tasks[task_id].completed_at = datetime.now().isoformat()
                    self._tasks_by_inventory.pop(self._tasks[task_id].inventory_id, None)

    def create_task(
        self,
        inventory_id: int,
        file_path: str,
        file_size: int,
        source_location_id: int,
        source_location_name: str,
        target_location_id: int,
        target_location_name: str
    ) -> str:
        """
        Create a new relocation task.

        Args:
            inventory_id: The file inventory ID
            file_path: Current file path
            file_size: File size in bytes
            source_location_id: Source storage location ID
            source_location_name: Source storage location name
            target_location_id: Target storage location ID
            target_location_name: Target storage location name

        Returns:
            task_id: Unique identifier for this task
        """
        with self._lock:
            # Check if there's already an active task for this file
            if inventory_id in self._tasks_by_inventory:
                existing_task_id = self._tasks_by_inventory[inventory_id]
                existing_task = self._tasks.get(existing_task_id)
                if existing_task and existing_task.status in ["pending", "running"]:
                    raise ValueError(f"A relocation task is already in progress for this file")

            task_id = str(uuid.uuid4())
            task = RelocationTask(
                task_id=task_id,
                inventory_id=inventory_id,
                file_path=file_path,
                source_location_id=source_location_id,
                source_location_name=source_location_name,
                target_location_id=target_location_id,
                target_location_name=target_location_name,
                status="pending",
                created_at=datetime.now().isoformat(),
                bytes_total=file_size
            )

            self._tasks[task_id] = task
            self._tasks_by_inventory[inventory_id] = task_id
            self._task_queue.append(task_id)

            logger.info(f"Created relocation task {task_id}: {file_path} -> {target_location_name}")
            return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        """
        Get task status by task ID.

        Args:
            task_id: The task identifier

        Returns:
            Task dictionary or None if not found
        """
        with self._lock:
            if task_id not in self._tasks:
                return None
            return self._tasks[task_id].to_dict()

    def get_task_for_inventory(self, inventory_id: int) -> Optional[dict]:
        """
        Get active task for an inventory entry.

        Args:
            inventory_id: The file inventory ID

        Returns:
            Task dictionary or None if no active task
        """
        with self._lock:
            task_id = self._tasks_by_inventory.get(inventory_id)
            if not task_id or task_id not in self._tasks:
                return None
            return self._tasks[task_id].to_dict()

    def get_all_active_tasks(self) -> List[dict]:
        """
        Get all active (pending or running) tasks.

        Returns:
            List of active task dictionaries
        """
        with self._lock:
            return [
                task.to_dict()
                for task in self._tasks.values()
                if task.status in ["pending", "running"]
            ]

    def get_recent_tasks(self, limit: int = 20) -> List[dict]:
        """
        Get recent tasks (active and recently completed).

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of task dictionaries, sorted by creation time (newest first)
        """
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True
            )[:limit]
            return [task.to_dict() for task in tasks]


# Global singleton instance
relocation_manager = RelocationTaskManager()
