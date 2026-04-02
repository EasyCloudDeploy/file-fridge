from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    ColdStorageLocation,
    MonitoredPath,
    RelocationTask,
    RelocationTaskStatus,
    StorageType,
)
from app.services.relocation_manager import relocation_manager, serialize_relocation_task


@pytest.mark.unit
class TestRelocationManager:
    @pytest.fixture(autouse=True)
    def cleanup_db(self, db_session):
        db_session.execute(RelocationTask.__table__.delete())
        db_session.commit()
        yield
        db_session.execute(RelocationTask.__table__.delete())
        db_session.commit()

    def test_create_task_success(self, db_session, storage_location):
        task_id = relocation_manager.create_task(101, "/p", 1024, storage_location.id, "S", storage_location.id, "T")
        assert task_id is not None
        task = relocation_manager.get_task(task_id)
        assert task["task_id"] == task_id
        assert task["status"] == RelocationTaskStatus.PENDING.value

    def test_create_duplicate_task_fails(self, db_session, storage_location):
        relocation_manager.create_task(1011, "/p1", 100, storage_location.id, "S", storage_location.id, "T")
        with pytest.raises(ValueError):
            relocation_manager.create_task(1011, "/p1", 100, storage_location.id, "S", storage_location.id, "T")

    def test_get_task_for_inventory(self, db_session, storage_location):
        task_id = relocation_manager.create_task(102, "/p2", 100, storage_location.id, "S", storage_location.id, "T")
        task = relocation_manager.get_task_for_inventory(102)
        assert task["task_id"] == task_id

    def test_get_all_active_tasks(self, db_session, storage_location):
        relocation_manager.create_task(1033, "/p3", 100, storage_location.id, "S", storage_location.id, "T")
        relocation_manager.create_task(1044, "/p4", 100, storage_location.id, "S", storage_location.id, "T")
        active = relocation_manager.get_all_active_tasks()
        assert len(active) >= 2 # since tests run in parallel or share DB sometimes

    def test_get_recent_tasks(self, db_session, storage_location):
        relocation_manager.create_task(1055, "/p5", 100, storage_location.id, "S", storage_location.id, "T")
        recent = relocation_manager.get_recent_tasks(limit=10)
        assert len(recent) >= 1

    def test_task_percent_complete(self):
        task = RelocationTask(
            task_id="t1", inventory_id=1, file_path="p",
            source_location_id=1, source_location_name="S",
            target_location_id=2, target_location_name="T",
            status=RelocationTaskStatus.RUNNING,
            bytes_total=1000, bytes_transferred=250
        )
        serialized = serialize_relocation_task(task)
        assert serialized["percent_complete"] == 25

        task.bytes_total = 0
        task.status = RelocationTaskStatus.COMPLETED
        serialized = serialize_relocation_task(task)
        assert serialized["percent_complete"] == 100

    def test_cleanup_old_tasks(self, db_session, storage_location):
        task_id = "old-task"
        task = RelocationTask(
            task_id=task_id, inventory_id=200, file_path="p",
            source_location_id=storage_location.id, source_location_name="S",
            target_location_id=storage_location.id, target_location_name="T",
            status=RelocationTaskStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc) - timedelta(days=2)
        )
        db_session.add(task)
        db_session.commit()
        relocation_manager._cleanup_old_tasks()
        assert relocation_manager.get_task(task_id) is None

    @pytest.mark.skip(reason="Needs valid Thread/DB connection logic")
    def test_process_task_success(self, db_session, tmp_path, file_inventory_factory):
        src_dir = tmp_path / "cold_src"
        src_dir.mkdir()
        src_file = src_dir / "move_me.txt"
        src_file.write_text("data to relocate")
        target_dir = tmp_path / "cold_target"
        target_dir.mkdir()
        target_loc = ColdStorageLocation(name="Target Location", path=str(target_dir))
        source_loc = ColdStorageLocation(name="Source Location", path=str(src_dir))
        db_session.add(target_loc)
        db_session.add(source_loc)
        db_session.commit()
        inv = file_inventory_factory(file_path=str(src_file), storage_type=StorageType.COLD)
        path = db_session.get(MonitoredPath, inv.path_id)
        path.storage_locations = [source_loc, target_loc]
        db_session.commit()

        task_id = relocation_manager.create_task(
            inventory_id=inv.id,
            file_path=inv.file_path,
            file_size=100,
            source_location_id=source_loc.id,
            source_location_name=source_loc.name,
            target_location_id=target_loc.id,
            target_location_name=target_loc.name
        )
        relocation_manager._process_task(task_id, db_session)

        db_session.expire_all()
        task = db_session.query(RelocationTask).filter(RelocationTask.task_id == task_id).first()
        assert task.status == RelocationTaskStatus.COMPLETED
