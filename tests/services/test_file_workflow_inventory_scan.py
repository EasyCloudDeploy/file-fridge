"""Coverage for inventory scan helpers in file_workflow_service."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import (
    ColdStorageBackendType,
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    ScanStatus,
    StorageType,
)
from app.services.file_workflow_service import FileWorkflowService


@pytest.fixture
def workflow_path(db_session, tmp_path):
    hot = tmp_path / "hot_inv"
    hot.mkdir()
    cold = tmp_path / "cold_inv"
    cold.mkdir()
    cold_loc = ColdStorageLocation(name="InvCold", path=str(cold))
    db_session.add(cold_loc)
    db_session.flush()
    path = MonitoredPath(
        name="Inv Path",
        source_path=str(hot),
        operation_type=OperationType.MOVE.value,
        last_scan_status=ScanStatus.SUCCESS,
    )
    path.storage_locations.append(cold_loc)
    db_session.add(path)
    db_session.commit()
    db_session.refresh(path)
    return path, hot, cold, cold_loc


@pytest.mark.unit
class TestScanFlatList:
    def test_returns_empty_for_missing_directory(self):
        service = FileWorkflowService()
        assert service._scan_flat_list("/no/such/directory") == []

    def test_collects_file_metadata_skips_symlinks(self, tmp_path):
        root = tmp_path / "scan_root"
        root.mkdir()
        real_file = root / "real.txt"
        real_file.write_text("hello")
        symlink = root / "link.txt"
        symlink.symlink_to(real_file)

        service = FileWorkflowService()
        results = service._scan_flat_list(str(root))
        paths = {item["path"] for item in results}
        assert str(real_file) in paths
        assert str(symlink) not in paths
        assert results[0]["size"] == 5
        assert isinstance(results[0]["mtime"], datetime)


@pytest.mark.unit
class TestUpdateFileInventory:
    def test_uses_provided_hot_and_cold_lists(self, workflow_path, db_session):
        path, hot, cold, cold_loc = workflow_path
        hot_file = hot / "a.dat"
        hot_file.write_bytes(b"aa")
        cold_file = cold / "b.dat"
        cold_file.write_bytes(b"bbb")
        now = datetime.now(tz=timezone.utc)

        hot_meta = [
            {
                "path": str(hot_file),
                "size": 2,
                "mtime": now,
                "atime": now,
                "ctime": now,
            }
        ]
        cold_meta = [
            {
                "path": str(cold_file),
                "size": 3,
                "mtime": now,
                "atime": now,
                "ctime": now,
                "location_id": cold_loc.id,
            }
        ]

        service = FileWorkflowService()
        with patch.object(service, "_sync_remote_cold_inventory", return_value=0):
            count = service._update_file_inventory(
                path, db_session, hot_files=hot_meta, cold_files=cold_meta, scan_start_time=now
            )

        assert count >= 2
        assert (
            db_session.query(FileInventory)
            .filter(FileInventory.path_id == path.id, FileInventory.storage_type == StorageType.HOT)
            .count()
            == 1
        )
        assert (
            db_session.query(FileInventory)
            .filter(
                FileInventory.path_id == path.id, FileInventory.storage_type == StorageType.COLD
            )
            .count()
            == 1
        )

    def test_scans_local_cold_and_skips_remote_backends(
        self, workflow_path, db_session, monkeypatch
    ):
        path, hot, cold, cold_loc = workflow_path
        remote = ColdStorageLocation(
            name="S3",
            path="s3://bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        path.storage_locations.append(remote)
        db_session.add(remote)
        db_session.commit()

        (hot / "only_hot.txt").write_text("x")
        (cold / "cold_only.txt").write_text("y")

        service = FileWorkflowService()

        def fake_get_backend(loc):
            if loc.backend_type == ColdStorageBackendType.S3:
                raise RuntimeError("no backend")
            backend = MagicMock()
            backend.backend_name.return_value = "local"
            return backend

        monkeypatch.setattr("app.services.file_workflow_service.get_backend", fake_get_backend)

        with patch.object(service, "_sync_remote_cold_inventory", return_value=0):
            count = service._update_file_inventory(path, db_session)

        assert count >= 2

    def test_deletes_stale_hot_inventory_entries(self, workflow_path, db_session):
        path, hot, _cold, _cold_loc = workflow_path
        stale = FileInventory(
            path_id=path.id,
            file_path=str(hot / "vanished.txt"),
            file_size=1,
            file_mtime=datetime.now(tz=timezone.utc) - timedelta(days=2),
            storage_type=StorageType.HOT,
            status=FileStatus.ACTIVE,
            last_seen=datetime.now(tz=timezone.utc) - timedelta(days=2),
        )
        db_session.add(stale)
        db_session.commit()
        stale_id = stale.id

        scan_time = datetime.now(tz=timezone.utc)
        service = FileWorkflowService()
        with patch.object(service, "_sync_remote_cold_inventory", return_value=0):
            service._update_file_inventory(
                path, db_session, hot_files=[], cold_files=[], scan_start_time=scan_time
            )

        assert db_session.query(FileInventory).filter(FileInventory.id == stale_id).count() == 0

    def test_sync_remote_cold_inventory_delegates_to_helper(
        self, workflow_path, db_session, monkeypatch
    ):
        path, _hot, _cold, _cold_loc = workflow_path
        service = FileWorkflowService()
        monkeypatch.setattr(service, "_sync_single_remote_location", lambda *_a, **_k: 2)
        gdrive = ColdStorageLocation(
            name="GDrive Sync",
            path="gdrive://folder",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        path.storage_locations.append(gdrive)
        db_session.add(gdrive)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "gdrive"
        mock_backend.list_managed_files = MagicMock()
        monkeypatch.setattr(
            "app.services.file_workflow_service.get_backend", lambda _loc: mock_backend
        )

        synced = service._sync_remote_cold_inventory(
            path, db_session, datetime.now(tz=timezone.utc)
        )
        assert synced == 4


@pytest.mark.unit
class TestRemoteSyncAndCriteriaHelpers:
    def test_sync_single_remote_location_paginates(self, workflow_path, db_session, monkeypatch):
        path, _hot, _cold, _cold_loc = workflow_path
        gdrive = ColdStorageLocation(
            name="Paged GDrive",
            path="gdrive://paged",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        db_session.add(gdrive)
        path.storage_locations.append(gdrive)
        db_session.commit()

        pages = [
            {"files": [{"id": "1", "name": "a.txt", "is_managed": False}], "next_page_token": "t2"},
            {"files": [{"id": "2", "name": "b.txt", "is_managed": False}], "next_page_token": None},
        ]

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "gdrive"
        mock_backend.build_reference.side_effect = lambda _loc, fid: f"gdrive://paged/{fid}"
        mock_backend.list_all_folder_files.side_effect = pages
        monkeypatch.setattr(
            "app.services.file_workflow_service.get_backend", lambda _loc: mock_backend
        )

        service = FileWorkflowService()
        synced = service._sync_single_remote_location(
            path, db_session, gdrive, mock_backend, datetime.now(tz=timezone.utc)
        )
        assert synced == 2

    def test_sync_remote_file_heal_encrypted_filename(self, workflow_path, db_session):
        path, hot, _cold, cold_loc = workflow_path
        service = FileWorkflowService()
        inv = FileInventory(
            path_id=path.id,
            file_path="gdrive://old/ref",
            file_size=10,
            file_mtime=datetime.now(tz=timezone.utc),
            storage_type=StorageType.COLD,
            is_encrypted=True,
        )
        db_session.add(inv)
        db_session.flush()
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot / "nested" / "secret.bin"),
            cold_storage_path="gdrive://old/ref",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        ctx = {
            "db": db_session,
            "path": path,
            "location": cold_loc,
            "existing_by_ref": {inv.file_path: inv},
            "scan_time": datetime.now(tz=timezone.utc),
        }
        managed, encrypted, rel = service._sync_remote_file_heal(
            {"name": f"ffenc_{inv.id}.ffenc", "id": "new-id"},
            "gdrive://new/ref",
            ctx,
        )
        assert managed is True
        assert encrypted is True
        assert "secret.bin" in rel
        assert inv.file_path == "gdrive://new/ref"

    def test_match_inventory_criteria_time_and_name(self, workflow_path, db_session):
        from app.models import Criteria, CriterionType, Operator

        path, _hot, _cold, _loc = workflow_path
        service = FileWorkflowService()
        now = datetime.now(tz=timezone.utc)
        inv = FileInventory(
            path_id=path.id,
            file_path="/cold/sample.txt",
            file_size=50,
            file_mtime=now,
            file_atime=now,
            file_ctime=now,
            storage_type=StorageType.COLD,
        )
        criteria = [
            Criteria(
                path_id=path.id,
                criterion_type=CriterionType.MTIME,
                operator=Operator.LT,
                value="999999",
                enabled=True,
            ),
            Criteria(
                path_id=path.id,
                criterion_type=CriterionType.NAME,
                operator=Operator.EQ,
                value="sample.txt",
                enabled=True,
            ),
            Criteria(
                path_id=path.id,
                criterion_type=CriterionType.TYPE,
                operator=Operator.EQ,
                value="file",
                enabled=True,
            ),
        ]
        matches, ids = service._match_inventory_criteria(Path("/hot/sample.txt"), inv, criteria)
        assert matches is True
        assert len(ids) == 3
