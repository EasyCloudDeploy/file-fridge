import pytest
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    StorageType,
)
from app.services.relocation_manager import relocation_manager, RelocationTask


@pytest.mark.unit
class TestRelocationManager:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """Reset the global relocation manager state."""
        with relocation_manager._lock:
            relocation_manager._tasks.clear()
            relocation_manager._tasks_by_inventory.clear()
            relocation_manager._task_queue.clear()
        yield

    def test_create_task_success(self):
        """Test successful creation of a relocation task."""
        task_id = relocation_manager.create_task(
            inventory_id=101,
            file_path="/data/hot/file.txt",
            file_size=1024,
            source_location_id=1,
            source_location_name="Hot",
            target_location_id=2,
            target_location_name="Cold"
        )
        assert task_id is not None
        
        task = relocation_manager.get_task(task_id)
        assert task["task_id"] == task_id
        assert task["status"] == "pending"
        assert task["inventory_id"] == 101

    def test_create_duplicate_task_fails(self):
        """Test that duplicate tasks for the same inventory item are prevented."""
        relocation_manager.create_task(101, "/p1", 100, 1, "S", 2, "T")
        
        with pytest.raises(ValueError, match="already in progress"):
            relocation_manager.create_task(101, "/p1", 100, 1, "S", 2, "T")

    def test_get_task_for_inventory(self):
        """Test getting task status by inventory ID."""
        task_id = relocation_manager.create_task(102, "/p2", 100, 1, "S", 2, "T")
        
        task = relocation_manager.get_task_for_inventory(102)
        assert task is not None
        assert task["task_id"] == task_id

    def test_get_all_active_tasks(self):
        """Test listing all active tasks."""
        relocation_manager.create_task(103, "/p3", 100, 1, "S", 2, "T")
        relocation_manager.create_task(104, "/p4", 100, 1, "S", 2, "T")
        
        active = relocation_manager.get_all_active_tasks()
        assert len(active) == 2

    def test_get_recent_tasks(self):
        """Test getting recent tasks list."""
        relocation_manager.create_task(105, "/p5", 100, 1, "S", 2, "T")
        
        recent = relocation_manager.get_recent_tasks(limit=10)
        assert len(recent) == 1
        assert recent[0]["inventory_id"] == 105

    def test_task_percent_complete(self):
        """Test percentage calculation in RelocationTask."""
        task = RelocationTask(
            task_id="t1", inventory_id=1, file_path="p",
            source_location_id=1, source_location_name="S",
            target_location_id=2, target_location_name="T",
            status="running", created_at="now",
            bytes_total=1000, bytes_transferred=250
        )
        assert task.percent_complete == 25
        
        task.bytes_transferred = 1000
        assert task.percent_complete == 100
        
        # Zero total case
        task.bytes_total = 0
        assert task.percent_complete == 100

    def test_cleanup_old_tasks(self):
        """Test cleaning up completed tasks."""
        task_id = "old-task"
        
        task = RelocationTask(
            task_id=task_id, inventory_id=200, file_path="p",
            source_location_id=1, source_location_name="S",
            target_location_id=2, target_location_name="T",
            status="completed", created_at="now",
            completed_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        )
        
        with relocation_manager._lock:
            relocation_manager._tasks[task_id] = task
            relocation_manager._tasks_by_inventory[200] = task_id
            original_interval = relocation_manager._cleanup_interval
            relocation_manager._cleanup_interval = 3600
            
        relocation_manager._cleanup_old_tasks()
        assert relocation_manager.get_task(task_id) is None
        relocation_manager._cleanup_interval = original_interval

    def test_process_task_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
        """Test the internal _process_task method directly."""
        # Setup source and target dirs
        src_dir = tmp_path / "cold_src"
        src_dir.mkdir()
        src_file = src_dir / "move_me.txt"
        src_file.write_text("data to relocate")
        
        target_dir = tmp_path / "cold_target"
        target_dir.mkdir()
        
        # Target storage location
        target_loc = ColdStorageLocation(name="Target Location", path=str(target_dir))
        db_session.add(target_loc)
        
        # Create inventory and path
        inv = file_inventory_factory(path=str(src_file), storage_type=StorageType.COLD)
        path = db_session.get(MonitoredPath, inv.path_id)
        # Fix the monitored path to point to our temp source
        path.storage_locations = [
            ColdStorageLocation(name="Source Location", path=str(src_dir)),
            target_loc
        ]
        db_session.commit()
        
        # Manually create task
        task_id = "process-test"
        task = RelocationTask(
            task_id=task_id, inventory_id=inv.id, file_path=inv.file_path,
            source_location_id=path.storage_locations[0].id, 
            source_location_name=path.storage_locations[0].name,
            target_location_id=target_loc.id, 
            target_location_name=target_loc.name,
            status="pending", created_at=datetime.now(timezone.utc).isoformat()
        )
        with relocation_manager._lock:
            relocation_manager._tasks[task_id] = task
            
        # Process task
        relocation_manager._process_task(task_id, db_session)
        
        assert task.status == "completed"
        assert not src_file.exists()
        assert Path(task.new_file_path).exists()
        assert Path(task.new_file_path).read_text() == "data to relocate"
        
        # Verify DB updated
        db_session.refresh(inv)
        assert inv.file_path == task.new_file_path
        assert inv.status == FileStatus.ACTIVE
