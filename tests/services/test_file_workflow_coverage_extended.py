"""Extended coverage tests for app/services/file_workflow_service.py."""

from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import (
    ColdStorageLocation,
    Criteria,
    CriterionType,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    Operator,
    ScanStatus,
    StorageType,
)
from app.services.file_workflow_service import FileWorkflowService


@pytest.fixture
def monitored_path(db_session, tmp_path):
    hot_dir = tmp_path / "hot_ext"
    hot_dir.mkdir(exist_ok=True)
    cold_dir = tmp_path / "cold_ext"
    cold_dir.mkdir(exist_ok=True)
    cold_loc = ColdStorageLocation(name="ExtColdLoc", path=str(cold_dir))
    db_session.add(cold_loc)
    db_session.flush()
    path = MonitoredPath(
        name="Ext Path",
        source_path=str(hot_dir),
        operation_type="move",
        last_scan_status=ScanStatus.SUCCESS,
    )
    path.storage_locations.append(cold_loc)
    db_session.add(path)
    db_session.commit()
    db_session.refresh(path)
    return path


def _resolved_future(fn, *args, **kwargs):
    future = Future()
    try:
        future.set_result(fn(*args, **kwargs))
    except Exception as exc:
        future.set_exception(exc)
    return future


@pytest.mark.unit
@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.FileWorkflowService._scan_path")
class TestProcessPathExtendedBranches:
    def test_thaw_errors_and_stop_during_thaw(
        self, mock_scan_path, mock_scan_progress, monitored_path, db_session
    ):
        mock_scan_progress.start_scan.return_value = ("scan-thaw", True)
        mock_scan_progress.is_stop_requested.side_effect = [False, True]
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [(Path("/hot/link.txt"), Path("/cold/file.txt"))],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 1,
        }

        service = FileWorkflowService()
        with patch.object(
            service, "_thaw_single_file", return_value={"success": False, "error": "x"}
        ), patch("app.services.file_workflow_service.ThreadPoolExecutor") as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            result = service.process_path(monitored_path, db_session)

        assert result["errors"]
        assert monitored_path.last_scan_status == ScanStatus.STOPPED

    @patch("app.services.file_workflow_service.FileCleanup.cleanup_missing_files")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_duplicates")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_symlink_inventory_entries")
    @patch("app.services.file_workflow_service.FileReconciliation.reconcile_missing_symlinks")
    @patch("app.services.file_workflow_service.FileWorkflowService._process_single_file")
    def test_freeze_errors_stop_and_cleanup(
        self,
        mock_process_single,
        mock_reconcile,
        mock_cleanup_symlinks,
        mock_cleanup_duplicates,
        mock_cleanup_missing,
        mock_scan_path,
        mock_scan_progress,
        monitored_path,
        db_session,
    ):
        mock_scan_progress.start_scan.return_value = ("scan-freeze", True)
        mock_scan_progress.is_stop_requested.return_value = False
        file_to_move = Path(monitored_path.source_path) / "move_me.txt"
        mock_scan_path.return_value = {
            "to_cold": [(file_to_move, [1])],
            "to_hot": [],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 1,
        }
        mock_process_single.return_value = {"success": False, "error": "move failed"}
        mock_cleanup_missing.return_value = {"removed": 1, "errors": ["cleanup missing err"]}
        mock_cleanup_duplicates.return_value = {"removed": 0, "errors": []}
        mock_cleanup_symlinks.return_value = {"removed": 0, "errors": []}
        mock_reconcile.return_value = {"symlinks_created": 0, "errors": ["reconcile err"]}

        service = FileWorkflowService()
        with patch("app.services.file_workflow_service.ThreadPoolExecutor") as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            result = service.process_path(monitored_path, db_session)

        assert any("move failed" in err for err in result["errors"])
        assert any("cleanup missing err" in err for err in result["errors"])
        assert any("reconcile err" in err for err in result["errors"])
        assert monitored_path.last_scan_status == ScanStatus.FAILURE

    @patch("app.services.file_workflow_service.FileCleanup.cleanup_missing_files")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_duplicates")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_symlink_inventory_entries")
    @patch("app.services.file_workflow_service.FileReconciliation.reconcile_missing_symlinks")
    @patch("app.services.file_workflow_service.FileWorkflowService._process_single_file")
    def test_partial_success_marks_failure_status(
        self,
        mock_process_single,
        mock_reconcile,
        mock_cleanup_symlinks,
        mock_cleanup_duplicates,
        mock_cleanup_missing,
        mock_scan_path,
        mock_scan_progress,
        monitored_path,
        db_session,
    ):
        mock_scan_progress.finish_scan.side_effect = None
        mock_scan_progress.start_scan.return_value = ("scan-partial", True)
        mock_scan_progress.is_stop_requested.return_value = False
        mock_scan_path.return_value = {
            "to_cold": [(Path(monitored_path.source_path) / "a.txt", [1])],
            "to_hot": [],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 1,
        }
        mock_process_single.return_value = {"success": False, "error": "partial err"}
        mock_cleanup_missing.return_value = {"removed": 0, "errors": []}
        mock_cleanup_duplicates.return_value = {"removed": 0, "errors": []}
        mock_cleanup_symlinks.return_value = {"removed": 0, "errors": []}
        mock_reconcile.return_value = {"symlinks_created": 0, "errors": []}

        service = FileWorkflowService()
        with patch("app.services.file_workflow_service.ThreadPoolExecutor") as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            service.process_path(monitored_path, db_session)

        assert monitored_path.last_scan_status == ScanStatus.FAILURE

    def test_inner_scan_exception(
        self, mock_scan_path, mock_scan_progress, monitored_path, db_session
    ):
        mock_scan_progress.start_scan.return_value = ("scan-exc", True)
        mock_scan_path.side_effect = RuntimeError("scan blew up")

        service = FileWorkflowService()
        result = service.process_path(monitored_path, db_session)

        assert "scan blew up" in result["errors"][0]
        assert monitored_path.last_scan_status == ScanStatus.FAILURE

    def test_outer_unexpected_exception(
        self, mock_scan_path, mock_scan_progress, monitored_path, db_session
    ):
        mock_scan_progress.start_scan.return_value = ("scan-outer", True)
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 0,
        }
        mock_scan_progress.is_stop_requested.return_value = False
        mock_scan_progress.finish_scan.side_effect = [RuntimeError("finish failed"), None]

        service = FileWorkflowService()
        result = service.process_path(monitored_path, db_session)

        assert result["files_found"] == 0
        assert "Unexpected error" in result["errors"][0]
        mock_scan_progress.finish_scan.side_effect = None


@pytest.mark.unit
class TestScanPathExtended:
    def test_scan_path_unreachable_source(self, monitored_path, db_session):
        monitored_path.source_path = "/definitely/not/a/real/path/xyz"
        db_session.commit()
        service = FileWorkflowService()
        result = service._scan_path(monitored_path, db_session)
        assert result["to_cold"] == []
        assert result["inventory_updated"] == 0

    @patch(
        "app.services.file_workflow_service.check_atime_availability",
        return_value=(False, "no atime"),
    )
    def test_scan_path_aborts_when_atime_unavailable(self, _mock_atime, monitored_path, db_session):
        cold_loc = monitored_path.storage_locations[0]
        cold_loc.operation_mode = OperationType.SYMLINK
        criterion = Criteria(
            path_id=monitored_path.id,
            criterion_type=CriterionType.ATIME,
            operator=Operator.LT,
            value="7",
            enabled=True,
        )
        db_session.add(criterion)
        db_session.commit()

        service = FileWorkflowService()
        result = service._scan_path(monitored_path, db_session)
        assert result["to_cold"] == []
        assert monitored_path.error_message == "no atime"

    def test_scan_path_collects_local_cold_metadata(self, monitored_path, db_session, tmp_path):
        cold_root = Path(monitored_path.storage_locations[0].path)
        cold_file = cold_root / "cold_meta.bin"
        cold_file.write_bytes(b"cold-data")

        service = FileWorkflowService()
        with patch.object(service, "_update_file_inventory", return_value=1):
            with patch.object(service, "_collect_cold_thaw_candidates", return_value=([], 0)):
                result = service._scan_path(monitored_path, db_session)

        assert result["inventory_updated"] == 1
        assert result["total_scanned"] >= 1


@pytest.mark.unit
class TestProcessSingleFileExtended:
    def test_process_single_file_skips_missing_inventory(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        hot_file = Path(monitored_path.source_path) / "missing_inventory.txt"
        hot_file.write_text("data")
        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)
        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=monitored_path.storage_locations[0],
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-1",
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)
        assert result["success"] is True
        assert result.get("skipped") is True

    def test_process_single_file_no_storage_location(self, monitored_path, db_session, tmp_path):
        hot_file = Path(monitored_path.source_path) / "no_storage.txt"
        hot_file.write_text("data")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        db_session.commit()

        service = FileWorkflowService()
        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=None,
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)
        assert result["success"] is False
        assert "No suitable storage location" in result["error"]

    def test_process_single_file_path_not_found(self, db_session, tmp_path, monkeypatch):
        ghost = MonitoredPath(
            name="Ghost", source_path=str(tmp_path / "ghost"), operation_type="move"
        )
        ghost.id = 999999
        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        result = service._process_single_file(Path("/tmp/nope.txt"), [1], ghost)
        assert result["success"] is False
        assert "Path not found" in (result["error"] or "")

    def test_process_single_file_skips_when_file_disappears(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        hot_file = Path(monitored_path.source_path) / "disappears.txt"
        hot_file.write_text("gone")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        db_session.commit()
        hot_file.unlink()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)
        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=monitored_path.storage_locations[0],
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-vanish",
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)
        assert result["success"] is True
        assert result.get("skipped") is True

    def test_process_single_file_stat_failure(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        hot_file = Path(monitored_path.source_path) / "stat_fail.txt"
        hot_file.write_text("data")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        db_session.commit()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)

        with patch.object(Path, "stat", side_effect=OSError("stat failed"), autospec=True), patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=monitored_path.storage_locations[0],
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-stat",
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)
        assert result["success"] is False
        assert "stat" in (result["error"] or "").lower()

    def test_process_single_file_local_move_success(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        hot_file = Path(monitored_path.source_path) / "local_move.txt"
        hot_file.write_text("payload")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=hot_file.stat().st_size,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        db_session.commit()

        cold_loc = monitored_path.storage_locations[0]
        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)

        with patch("app.services.file_workflow_service.get_backend", return_value=None), patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=cold_loc,
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-local",
        ), patch(
            "app.services.file_workflow_service.FileMover.move_with_rollback",
            return_value=(True, None, "checksum-after"),
        ), patch.object(
            service, "_record_file_in_db", return_value=42
        ) as mock_record, patch(
            "app.services.file_workflow_service.audit_trail_service.log_freeze_operation"
        ):
            result = service._process_single_file(hot_file, [9], monitored_path)

        assert result["success"] is True
        assert result["file_record_id"] == 42
        mock_record.assert_called_once()

    def test_process_single_file_remote_freeze_failure(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType

        hot_file = Path(monitored_path.source_path) / "remote_fail.txt"
        hot_file.write_text("x")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        db_session.commit()

        remote_loc = ColdStorageLocation(
            name="Remote",
            path="s3://bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(remote_loc)
        db_session.commit()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)

        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=remote_loc,
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-remote",
        ), patch(
            "app.services.file_workflow_service.FileFreezer.freeze_file",
            return_value=(False, "upload failed", None),
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)

        assert result["success"] is False
        assert "upload failed" in result["error"]


@pytest.mark.unit
@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.FileWorkflowService._scan_path")
class TestProcessPathSuccessAndStopBranches:
    def test_thaw_success_increments_files_moved(
        self, mock_scan_path, mock_scan_progress, monitored_path, db_session
    ):
        mock_scan_progress.start_scan.return_value = ("scan-thaw-ok", True)
        mock_scan_progress.is_stop_requested.return_value = False
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [(Path("/hot/link.txt"), Path("/cold/file.txt"))],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 1,
        }

        service = FileWorkflowService()
        with patch.object(service, "_thaw_single_file", return_value={"success": True}), patch(
            "app.services.file_workflow_service.ThreadPoolExecutor"
        ) as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            result = service.process_path(monitored_path, db_session)

        assert result["files_moved"] == 1
        assert not result["errors"]

    def test_stop_during_freeze_phase(
        self, mock_scan_path, mock_scan_progress, monitored_path, db_session
    ):
        mock_scan_progress.start_scan.return_value = ("scan-freeze-stop", True)
        stop_checks = iter([False, True])

        def _stop_requested(_path_id):
            return next(stop_checks, True)

        mock_scan_progress.is_stop_requested.side_effect = _stop_requested
        hot_file = Path(monitored_path.source_path) / "freeze_stop.txt"
        hot_file.write_text("stop me")
        mock_scan_path.return_value = {
            "to_cold": [(hot_file, [1])],
            "to_hot": [],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 1,
        }

        service = FileWorkflowService()
        with patch.object(service, "_process_single_file", return_value={"success": True}), patch(
            "app.services.file_workflow_service.ThreadPoolExecutor"
        ) as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            result = service.process_path(monitored_path, db_session)

        assert monitored_path.last_scan_status == ScanStatus.STOPPED
        assert result["files_moved"] == 1
        mock_scan_progress.finish_scan.assert_any_call(monitored_path.id, status="stopped")

    @patch("app.services.file_workflow_service.FileCleanup.cleanup_missing_files")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_duplicates")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_symlink_inventory_entries")
    @patch("app.services.file_workflow_service.FileReconciliation.reconcile_missing_symlinks")
    def test_successful_scan_marks_success_status(
        self,
        mock_reconcile,
        mock_cleanup_symlinks,
        mock_cleanup_duplicates,
        mock_cleanup_missing,
        mock_scan_path,
        mock_scan_progress,
        monitored_path,
        db_session,
    ):
        mock_scan_progress.start_scan.return_value = ("scan-success", True)
        mock_scan_progress.is_stop_requested.return_value = False
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [],
            "inventory_updated": 3,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 3,
        }
        mock_cleanup_missing.return_value = {"removed": 0, "errors": []}
        mock_cleanup_duplicates.return_value = {"removed": 0, "errors": []}
        mock_cleanup_symlinks.return_value = {"removed": 0, "errors": []}
        mock_reconcile.return_value = {"symlinks_created": 0, "errors": []}

        service = FileWorkflowService()
        service.process_path(monitored_path, db_session)
        assert monitored_path.last_scan_status == ScanStatus.SUCCESS


@pytest.mark.unit
class TestRemoteInventorySyncExtended:
    def test_sync_single_remote_file_creates_external_inventory(self, monitored_path, db_session):
        from app.models import ColdStorageBackendType

        remote_loc = ColdStorageLocation(
            name="Remote Sync",
            path="gdrive://sync-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        db_session.add(remote_loc)
        monitored_path.storage_locations.append(remote_loc)
        db_session.commit()

        service = FileWorkflowService()
        ctx = {
            "db": db_session,
            "path": monitored_path,
            "location": remote_loc,
            "existing_by_ref": {},
            "scan_time": datetime.now(timezone.utc),
        }
        remote_file = {
            "id": "external-id",
            "name": "external.txt",
            "size": 12,
            "modified_time": "2024-01-01T00:00:00Z",
            "mime_type": "text/plain",
            "is_managed": False,
        }

        mock_backend = MagicMock()
        mock_backend.build_reference.return_value = "gdrive://sync-folder/external-id"

        with patch("app.services.file_workflow_service.get_backend", return_value=mock_backend):
            assert service._sync_single_remote_file(remote_file, ctx) is True
        db_session.commit()

        created = (
            db_session.query(FileInventory)
            .filter(FileInventory.file_path == "gdrive://sync-folder/external-id")
            .one()
        )
        assert created.storage_type == StorageType.COLD

    def test_sync_single_remote_file_skips_other_location(self, monitored_path, db_session):
        from app.models import ColdStorageBackendType

        remote_loc = ColdStorageLocation(
            name="Remote Skip",
            path="gdrive://skip",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        db_session.add(remote_loc)
        db_session.commit()

        service = FileWorkflowService()
        ctx = {
            "db": db_session,
            "path": monitored_path,
            "location": remote_loc,
            "existing_by_ref": {},
            "scan_time": datetime.now(timezone.utc),
        }
        assert (
            service._sync_single_remote_file(
                {"id": "x", "ff_location_id": "999", "name": "other.txt"}, ctx
            )
            is False
        )

    def test_process_single_file_backend_freeze_success_copy(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType, FileRecord

        hot_file = Path(monitored_path.source_path) / "remote_copy.txt"
        hot_file.write_text("copy-me")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=7,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        db_session.commit()
        inv_id = inv.id

        remote_loc = ColdStorageLocation(
            name="S3 Target",
            path="s3://bucket",
            backend_type=ColdStorageBackendType.S3,
            operation_mode=OperationType.COPY,
        )
        db_session.add(remote_loc)
        db_session.commit()

        latest_record = FileRecord(
            path_id=monitored_path.id,
            original_path=str(hot_file),
            cold_storage_path="s3://bucket/remote_copy.txt",
            cold_storage_location_id=remote_loc.id,
            file_size=7,
            operation_type=OperationType.COPY,
        )
        db_session.add(latest_record)
        db_session.commit()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"

        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=remote_loc,
        ), patch(
            "app.services.file_workflow_service.get_backend", return_value=mock_backend
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-copy",
        ), patch(
            "app.services.file_workflow_service.FileFreezer.freeze_file",
            return_value=(True, None, "s3://bucket/remote_copy.txt"),
        ):
            result = service._process_single_file(hot_file, [3], monitored_path)

        assert result["success"] is True
        refreshed = db_session.get(FileInventory, inv_id)
        assert refreshed.storage_type == StorageType.HOT


@pytest.mark.unit
@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.FileWorkflowService._scan_path")
class TestProcessPathThawExceptionBranch:
    def test_thaw_future_exception_appends_error(
        self, mock_scan_path, mock_scan_progress, monitored_path, db_session
    ):
        mock_scan_progress.start_scan.return_value = ("scan-thaw-exc", True)
        mock_scan_progress.is_stop_requested.return_value = False
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [(Path("/hot/a.txt"), Path("/cold/a.txt"))],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 1,
        }

        service = FileWorkflowService()

        def _raise(*_args, **_kwargs):
            raise RuntimeError("thaw blew up")

        with patch.object(service, "_thaw_single_file", side_effect=_raise), patch(
            "app.services.file_workflow_service.ThreadPoolExecutor"
        ) as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            result = service.process_path(monitored_path, db_session)

        assert any("thaw blew up" in err for err in result["errors"])


@pytest.mark.unit
class TestThawSingleFileExtended:
    def test_thaw_skips_when_cold_missing_without_record(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        hot = Path(monitored_path.source_path) / "orphan_hot.txt"
        cold = Path(monitored_path.storage_locations[0].path) / "missing_cold.txt"
        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        with patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-skip",
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.complete_file_operation"
        ):
            result = service._thaw_single_file(hot, cold, monitored_path)
        assert result["success"] is True
        assert result.get("skipped") is True

    def test_thaw_local_move_without_record(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        cold_dir = Path(monitored_path.storage_locations[0].path)
        cold_file = cold_dir / "direct_thaw.bin"
        cold_file.write_bytes(b"cold-bytes")
        hot = Path(monitored_path.source_path) / "direct_thaw.bin"
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(cold_file),
            file_size=cold_file.stat().st_size,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        db_session.add(inv)
        db_session.commit()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        with patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-direct",
        ), patch(
            "app.services.file_workflow_service.FileMover.move_with_rollback",
            return_value=(True, None, "checksum"),
        ), patch(
            "app.services.file_workflow_service.audit_trail_service.log_thaw_operation"
        ):
            result = service._thaw_single_file(hot, cold_file, monitored_path)
        assert result["success"] is True

    def test_thaw_file_thawer_failure_restores_status(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        cold_dir = Path(monitored_path.storage_locations[0].path)
        cold_file = cold_dir / "thaw_fail.bin"
        cold_file.write_bytes(b"x")
        hot = Path(monitored_path.source_path) / "thaw_fail.bin"
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(cold_file),
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            status=FileStatus.ACTIVE,
        )
        record = FileRecord(
            path_id=monitored_path.id,
            original_path=str(hot),
            cold_storage_path=str(cold_file),
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()
        inv_id = inv.id

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        with patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-fail",
        ), patch(
            "app.services.file_workflow_service.FileThawer.thaw_file",
            return_value=(False, "thaw err"),
        ):
            result = service._thaw_single_file(hot, cold_file, monitored_path)
        assert result["success"] is False
        refreshed = db_session.get(FileInventory, inv_id)
        assert refreshed.status == FileStatus.ACTIVE


@pytest.mark.unit
@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.FileWorkflowService._scan_path")
class TestProcessPathCleanupExceptions:
    @patch("app.services.file_workflow_service.FileReconciliation.reconcile_missing_symlinks")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_symlink_inventory_entries")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_duplicates")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_missing_files")
    def test_cleanup_and_reconcile_exceptions_are_logged(
        self,
        mock_cleanup_missing,
        mock_cleanup_duplicates,
        mock_cleanup_symlinks,
        mock_reconcile,
        mock_scan_path,
        mock_scan_progress,
        monitored_path,
        db_session,
    ):
        mock_scan_progress.start_scan.return_value = ("scan-clean-exc", True)
        mock_scan_progress.is_stop_requested.return_value = False
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 0,
        }
        mock_cleanup_missing.side_effect = RuntimeError("cleanup boom")
        mock_cleanup_duplicates.return_value = {"removed": 0, "errors": []}
        mock_cleanup_symlinks.return_value = {"removed": 0, "errors": []}
        mock_reconcile.side_effect = RuntimeError("reconcile boom")

        service = FileWorkflowService()
        result = service.process_path(monitored_path, db_session)
        assert result["files_found"] == 0
        assert monitored_path.last_scan_status == ScanStatus.FAILURE
        assert any("Reconciliation error" in err for err in result["errors"])


@pytest.mark.unit
class TestSyncRemoteColdInventoryExtended:
    def test_sync_remote_cold_inventory_skips_local_and_errors(
        self, monitored_path, db_session, monkeypatch
    ):
        from app.models import ColdStorageBackendType

        service = FileWorkflowService()
        gdrive = ColdStorageLocation(
            name="Broken Remote",
            path="gdrive://broken",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        db_session.add(gdrive)
        monitored_path.storage_locations.append(gdrive)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.file_workflow_service.get_backend",
            MagicMock(side_effect=RuntimeError("backend down")),
        )
        synced = service._sync_remote_cold_inventory(
            monitored_path, db_session, datetime.now(tz=timezone.utc)
        )
        assert synced == 0

    def test_process_single_file_remote_backend_resolution_error(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType

        hot_file = Path(monitored_path.source_path) / "remote_backend_fail.txt"
        hot_file.write_text("remote")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=6,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        db_session.add(inv)
        remote_loc = monitored_path.storage_locations[0]
        remote_loc.backend_type = ColdStorageBackendType.S3
        remote_loc.path = "s3://bucket"
        db_session.commit()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)
        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=remote_loc,
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-remote-fail",
        ), patch(
            "app.services.file_workflow_service.get_backend",
            side_effect=RuntimeError("backend init failed"),
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)
        assert result["success"] is False

    def test_process_single_file_backend_resolution_failure_for_remote(
        self, monitored_path, db_session, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType

        hot_file = Path(monitored_path.source_path) / "backend_fail.txt"
        hot_file.write_text("x")
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(hot_file),
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        remote_loc = ColdStorageLocation(
            name="Broken S3",
            path="s3://broken",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add_all([inv, remote_loc])
        db_session.commit()

        service = FileWorkflowService()
        monkeypatch.setattr("app.services.file_workflow_service.SessionFactory", lambda: db_session)
        monkeypatch.setattr("app.services.file_workflow_service.time.sleep", lambda _s: None)
        with patch(
            "app.services.file_workflow_service.storage_routing_service.select_storage_location",
            return_value=remote_loc,
        ), patch(
            "app.services.file_workflow_service.get_backend",
            side_effect=RuntimeError("no backend"),
        ), patch(
            "app.services.file_workflow_service.scan_progress_manager.start_file_operation",
            return_value="op-backend-fail",
        ):
            result = service._process_single_file(hot_file, [1], monitored_path)
        assert result["success"] is False


@pytest.mark.unit
class TestCollectColdThawCandidatesExtended:
    def test_collect_cold_thaw_candidates_skips_pinned_and_missing_records(
        self, monitored_path, db_session, tmp_path
    ):
        from app.models import PinnedFile

        cold_dir = Path(monitored_path.storage_locations[0].path)
        cold_file = cold_dir / "thaw_candidate.bin"
        cold_file.write_bytes(b"c")
        hot = Path(monitored_path.source_path) / "thaw_candidate.bin"
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(cold_file),
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            status=FileStatus.ACTIVE,
        )
        record = FileRecord(
            path_id=monitored_path.id,
            original_path=str(hot),
            cold_storage_path=str(cold_file),
            cold_storage_location_id=monitored_path.storage_locations[0].id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        pinned_inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(cold_dir / "pinned.bin"),
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            status=FileStatus.ACTIVE,
        )
        pinned_record = FileRecord(
            path_id=monitored_path.id,
            original_path=str(Path(monitored_path.source_path) / "pinned.bin"),
            cold_storage_path=str(cold_dir / "pinned.bin"),
            cold_storage_location_id=monitored_path.storage_locations[0].id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record, pinned_inv, pinned_record])
        db_session.add(
            PinnedFile(
                path_id=monitored_path.id,
                file_path=str(Path(monitored_path.source_path) / "pinned.bin"),
            )
        )
        db_session.commit()

        pinned_hot = Path(monitored_path.source_path) / "pinned.bin"
        service = FileWorkflowService()
        with patch(
            "app.services.file_workflow_service.CriteriaMatcher.match_file",
            return_value=(True, [1]),
        ):
            candidates, skipped = service._collect_cold_thaw_candidates(
                monitored_path,
                db_session,
                {pinned_hot},
                {str(pinned_hot)},
            )
        assert len(candidates) == 1
        assert skipped >= 1

    def test_match_inventory_criteria_size_and_name(self, monitored_path, db_session, tmp_path):
        from app.models import Criteria, CriterionType, Operator

        criterion = Criteria(
            path_id=monitored_path.id,
            criterion_type=CriterionType.SIZE,
            operator=Operator.GT,
            value="0",
            enabled=True,
        )
        db_session.add(criterion)
        db_session.commit()
        inv = FileInventory(
            path_id=monitored_path.id,
            file_path=str(tmp_path / "x.bin"),
            file_size=100,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        service = FileWorkflowService()
        matches, ids = service._match_inventory_criteria(Path("/hot/x.bin"), inv, [criterion])
        assert matches is True
        assert ids == [criterion.id]


@pytest.mark.unit
@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.FileWorkflowService._scan_path")
class TestProcessPathThawCancelBranch:
    @patch("app.services.file_workflow_service.FileReconciliation.reconcile_missing_symlinks")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_symlink_inventory_entries")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_duplicates")
    @patch("app.services.file_workflow_service.FileCleanup.cleanup_missing_files")
    def test_stop_during_thaw_cancels_remaining_futures(
        self,
        mock_cleanup_missing,
        mock_cleanup_duplicates,
        mock_cleanup_symlinks,
        mock_reconcile,
        mock_scan_path,
        mock_scan_progress,
        monitored_path,
        db_session,
    ):
        mock_cleanup_missing.return_value = {"removed": 0, "errors": []}
        mock_cleanup_duplicates.return_value = {"removed": 0, "errors": []}
        mock_cleanup_symlinks.return_value = {"removed": 0, "errors": []}
        mock_reconcile.return_value = {"symlinks_created": 0, "errors": []}
        mock_scan_progress.start_scan.return_value = ("scan-cancel-thaw", True)
        mock_scan_progress.is_stop_requested.side_effect = [False, True]
        mock_scan_path.return_value = {
            "to_cold": [],
            "to_hot": [
                (Path("/hot/a.txt"), Path("/cold/a.txt")),
                (Path("/hot/b.txt"), Path("/cold/b.txt")),
            ],
            "inventory_updated": 0,
            "skipped_hot": 0,
            "skipped_cold": 0,
            "total_scanned": 2,
        }

        service = FileWorkflowService()
        with patch.object(service, "_thaw_single_file", return_value={"success": True}), patch(
            "app.services.file_workflow_service.ThreadPoolExecutor"
        ) as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit = _resolved_future
            service.process_path(monitored_path, db_session)

        assert monitored_path.last_scan_status in (ScanStatus.STOPPED, ScanStatus.FAILURE)
