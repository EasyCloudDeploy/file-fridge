from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileStatus,
    MonitoredPath,
    RelocationTask,
    RelocationTaskStatus,
    StorageType,
)
from app.services.relocation_manager import (
    RelocationTaskManager,
    relocation_manager,
    serialize_relocation_task,
)


@pytest.fixture
def quiet_relocation_manager(monkeypatch):
    """RelocationTaskManager without background worker/cleanup threads."""
    real_recover = RelocationTaskManager._recover_tasks
    monkeypatch.setattr(RelocationTaskManager, "_recover_tasks", lambda self: None)
    monkeypatch.setattr(RelocationTaskManager, "_start_worker_thread", lambda self: None)
    monkeypatch.setattr(RelocationTaskManager, "_start_cleanup_thread", lambda self: None)
    mgr = RelocationTaskManager()
    mgr._recover_tasks = real_recover.__get__(mgr, RelocationTaskManager)
    return mgr


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
        task_id = relocation_manager.create_task(
            101, "/p", 1024, storage_location.id, "S", storage_location.id, "T"
        )
        assert task_id is not None
        task = relocation_manager.get_task(task_id)
        assert task["task_id"] == task_id
        assert task["status"] == RelocationTaskStatus.PENDING.value

    def test_create_duplicate_task_fails(self, db_session, storage_location):
        relocation_manager.create_task(
            1011, "/p1", 100, storage_location.id, "S", storage_location.id, "T"
        )
        with pytest.raises(ValueError):
            relocation_manager.create_task(
                1011, "/p1", 100, storage_location.id, "S", storage_location.id, "T"
            )

    def test_get_task_for_inventory(self, db_session, storage_location):
        task_id = relocation_manager.create_task(
            102, "/p2", 100, storage_location.id, "S", storage_location.id, "T"
        )
        task = relocation_manager.get_task_for_inventory(102)
        assert task["task_id"] == task_id

    def test_get_all_active_tasks(self, db_session, storage_location):
        relocation_manager.create_task(
            1033, "/p3", 100, storage_location.id, "S", storage_location.id, "T"
        )
        relocation_manager.create_task(
            1044, "/p4", 100, storage_location.id, "S", storage_location.id, "T"
        )
        active = relocation_manager.get_all_active_tasks()
        assert len(active) >= 2  # since tests run in parallel or share DB sometimes

    def test_get_recent_tasks(self, db_session, storage_location):
        relocation_manager.create_task(
            1055, "/p5", 100, storage_location.id, "S", storage_location.id, "T"
        )
        recent = relocation_manager.get_recent_tasks(limit=10)
        assert len(recent) >= 1

    def test_task_percent_complete(self):
        task = RelocationTask(
            task_id="t1",
            inventory_id=1,
            file_path="p",
            source_location_id=1,
            source_location_name="S",
            target_location_id=2,
            target_location_name="T",
            status=RelocationTaskStatus.RUNNING,
            bytes_total=1000,
            bytes_transferred=250,
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
            task_id=task_id,
            inventory_id=200,
            file_path="p",
            source_location_id=storage_location.id,
            source_location_name="S",
            target_location_id=storage_location.id,
            target_location_name="T",
            status=RelocationTaskStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        db_session.add(task)
        db_session.commit()
        relocation_manager._cleanup_old_tasks()
        assert relocation_manager.get_task(task_id) is None

    def test_process_task_success(
        self, db_session, tmp_path, file_inventory_factory, quiet_relocation_manager
    ):
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
        inv = file_inventory_factory(path=str(src_file), storage_type=StorageType.COLD)
        path = db_session.get(MonitoredPath, inv.path_id)
        path.storage_locations = [source_loc, target_loc]
        db_session.commit()

        task_id = quiet_relocation_manager.create_task(
            inventory_id=inv.id,
            file_path=inv.file_path,
            file_size=100,
            source_location_id=source_loc.id,
            source_location_name=source_loc.name,
            target_location_id=target_loc.id,
            target_location_name=target_loc.name,
        )
        quiet_relocation_manager._process_task(task_id, db_session)

        db_session.expire_all()
        task = db_session.query(RelocationTask).filter(RelocationTask.task_id == task_id).first()
        assert task.status == RelocationTaskStatus.COMPLETED
        assert task.new_file_path == str(target_dir / "move_me.txt")
        assert (target_dir / "move_me.txt").exists()
        refreshed_inv = db_session.get(FileInventory, inv.id)
        assert refreshed_inv.status == FileStatus.ACTIVE
        assert refreshed_inv.cold_storage_location_id == target_loc.id

    def test_process_task_missing_inventory_marks_failed(
        self, db_session, storage_location, quiet_relocation_manager
    ):
        task = RelocationTask(
            task_id="fail-inv",
            inventory_id=999999,
            file_path="/missing",
            source_location_id=storage_location.id,
            source_location_name="S",
            target_location_id=storage_location.id,
            target_location_name="T",
            status=RelocationTaskStatus.RUNNING,
        )
        db_session.add(task)
        db_session.commit()

        quiet_relocation_manager._process_task("fail-inv", db_session)

        db_session.expire_all()
        failed = db_session.query(RelocationTask).filter_by(task_id="fail-inv").one()
        assert failed.status == RelocationTaskStatus.FAILED
        assert "Inventory entry" in failed.error_message

    def test_process_task_missing_source_file_resets_migrating_status(
        self, db_session, tmp_path, file_inventory_factory, quiet_relocation_manager
    ):
        cold_dir = tmp_path / "cold_only"
        cold_dir.mkdir()
        missing_file = cold_dir / "gone.txt"
        inv = file_inventory_factory(
            path=str(missing_file),
            storage_type=StorageType.COLD,
            create_physical_file=False,
        )
        inv.status = FileStatus.MIGRATING
        loc = ColdStorageLocation(name="Cold", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()
        path = db_session.get(MonitoredPath, inv.path_id)
        path.storage_locations = [loc]
        db_session.commit()

        task_id = quiet_relocation_manager.create_task(
            inventory_id=inv.id,
            file_path=inv.file_path,
            file_size=10,
            source_location_id=loc.id,
            source_location_name=loc.name,
            target_location_id=loc.id,
            target_location_name=loc.name,
        )
        quiet_relocation_manager._process_task(task_id, db_session)

        db_session.expire_all()
        refreshed = db_session.get(FileInventory, inv.id)
        assert refreshed.status == FileStatus.ACTIVE
        task = db_session.query(RelocationTask).filter_by(task_id=task_id).one()
        assert task.status == RelocationTaskStatus.FAILED

    def test_process_task_progress_callback_updates_bytes(
        self, db_session, tmp_path, file_inventory_factory, quiet_relocation_manager, monkeypatch
    ):
        src_dir = tmp_path / "prog_src"
        src_dir.mkdir()
        src_file = src_dir / "big.bin"
        src_file.write_bytes(b"x" * 4096)
        target_dir = tmp_path / "prog_target"
        target_dir.mkdir()
        source_loc = ColdStorageLocation(name="Prog Source", path=str(src_dir))
        target_loc = ColdStorageLocation(name="Prog Target", path=str(target_dir))
        db_session.add_all([source_loc, target_loc])
        db_session.commit()

        inv = file_inventory_factory(path=str(src_file), storage_type=StorageType.COLD)
        path = db_session.get(MonitoredPath, inv.path_id)
        path.storage_locations = [source_loc, target_loc]
        db_session.commit()

        task_id = quiet_relocation_manager.create_task(
            inventory_id=inv.id,
            file_path=inv.file_path,
            file_size=4096,
            source_location_id=source_loc.id,
            source_location_name=source_loc.name,
            target_location_id=target_loc.id,
            target_location_name=target_loc.name,
        )

        times = iter([0.0, 0.0, 5.0, 5.0, 10.0, 10.0])
        monkeypatch.setattr("app.services.relocation_manager.time.time", lambda: next(times, 10.0))

        captured = []

        def _move_with_progress(source, dest, op, progress_callback=None):
            if progress_callback:
                progress_callback(2048)
                progress_callback(4096)
            return True, None

        monkeypatch.setattr(
            "app.services.relocation_manager.FileMover.move_file", _move_with_progress
        )

        quiet_relocation_manager._process_task(task_id, db_session)

        task = db_session.query(RelocationTask).filter_by(task_id=task_id).one()
        assert task.status == RelocationTaskStatus.COMPLETED
        assert task.bytes_transferred == 4096

    def test_recover_tasks_resets_running_to_pending(
        self, db_session, storage_location, quiet_relocation_manager
    ):
        running = RelocationTask(
            task_id="recover-me",
            inventory_id=1,
            file_path="/p",
            source_location_id=storage_location.id,
            source_location_name="S",
            target_location_id=storage_location.id,
            target_location_name="T",
            status=RelocationTaskStatus.RUNNING,
            bytes_transferred=500,
        )
        db_session.add(running)
        db_session.commit()

        quiet_relocation_manager._recover_tasks()

        db_session.expire_all()
        recovered = db_session.query(RelocationTask).filter_by(task_id="recover-me").one()
        assert recovered.status == RelocationTaskStatus.PENDING
        assert recovered.bytes_transferred == 0

    def test_process_task_remote_backend_success(
        self, db_session, tmp_path, file_inventory_factory, quiet_relocation_manager, monkeypatch
    ):
        src_dir = tmp_path / "remote_src"
        src_dir.mkdir()
        src_file = src_dir / "remote.txt"
        src_file.write_text("remote")
        source_loc = ColdStorageLocation(name="Local Source", path=str(src_dir))
        target_loc = ColdStorageLocation(name="S3 Target", path="s3://bucket/prefix")
        db_session.add_all([source_loc, target_loc])
        db_session.commit()

        inv = file_inventory_factory(path=str(src_file), storage_type=StorageType.COLD)
        path = db_session.get(MonitoredPath, inv.path_id)
        path.storage_locations = [source_loc, target_loc]
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.freeze_file.return_value = (True, None, "s3://bucket/prefix/remote.txt", "abc")
        monkeypatch.setattr(
            "app.services.relocation_manager.get_backend", lambda _loc: mock_backend
        )

        task_id = quiet_relocation_manager.create_task(
            inventory_id=inv.id,
            file_path=inv.file_path,
            file_size=src_file.stat().st_size,
            source_location_id=source_loc.id,
            source_location_name=source_loc.name,
            target_location_id=target_loc.id,
            target_location_name=target_loc.name,
        )
        quiet_relocation_manager._process_task(task_id, db_session)

        task = db_session.query(RelocationTask).filter_by(task_id=task_id).one()
        assert task.status == RelocationTaskStatus.COMPLETED
        assert task.new_file_path == "s3://bucket/prefix/remote.txt"

    def test_process_task_remote_backend_failure(
        self, db_session, tmp_path, file_inventory_factory, quiet_relocation_manager, monkeypatch
    ):
        src_dir = tmp_path / "remote_fail_src"
        src_dir.mkdir()
        src_file = src_dir / "fail_remote.txt"
        src_file.write_text("data")
        source_loc = ColdStorageLocation(name="Local Source", path=str(src_dir))
        target_loc = ColdStorageLocation(name="GDrive Target", path="gdrive://folder")
        db_session.add_all([source_loc, target_loc])
        db_session.commit()

        inv = file_inventory_factory(path=str(src_file), storage_type=StorageType.COLD)
        path = db_session.get(MonitoredPath, inv.path_id)
        path.storage_locations = [source_loc, target_loc]
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "gdrive"
        mock_backend.freeze_file.return_value = (False, "quota exceeded", None, None)
        monkeypatch.setattr(
            "app.services.relocation_manager.get_backend", lambda _loc: mock_backend
        )

        task_id = quiet_relocation_manager.create_task(
            inventory_id=inv.id,
            file_path=inv.file_path,
            file_size=4,
            source_location_id=source_loc.id,
            source_location_name=source_loc.name,
            target_location_id=target_loc.id,
            target_location_name=target_loc.name,
        )
        quiet_relocation_manager._process_task(task_id, db_session)

        task = db_session.query(RelocationTask).filter_by(task_id=task_id).one()
        assert task.status == RelocationTaskStatus.FAILED
        assert "quota exceeded" in (task.error_message or "")

    def test_process_task_not_found(self, db_session, quiet_relocation_manager):
        quiet_relocation_manager._process_task("missing-task-id", db_session)
