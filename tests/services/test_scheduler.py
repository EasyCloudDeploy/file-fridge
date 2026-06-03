from unittest.mock import MagicMock

import pytest

from app.models import ColdStorageLocation, MonitoredPath, StorageType
from app.services.scheduler import (
    decrypt_location_job_func,
    encrypt_location_job_func,
    scan_path_job_func,
)


@pytest.mark.unit
class TestSchedulerService:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        """Mock SchedulerSessionLocal to use the test db_session."""
        # Prevent the job from actually closing the session
        monkeypatch.setattr(db_session, "close", lambda: None)

        mock_session_factory = MagicMock(return_value=db_session)
        monkeypatch.setattr("app.services.scheduler.SchedulerSessionLocal", mock_session_factory)
        return mock_session_factory

    def test_scan_path_job_not_found(self):
        """Test scan job with non-existent path."""
        # Should not raise exception
        scan_path_job_func(9999)

    def test_scan_path_job_success(self, db_session, monitored_path_factory, monkeypatch):
        """Test the path scan job function."""
        path = monitored_path_factory("Scan Job Path", "/tmp/hot_job")

        from app.services.file_workflow_service import file_workflow_service

        mock_process = MagicMock(return_value={"files_moved": 5, "bytes_saved": 500, "errors": []})
        monkeypatch.setattr(file_workflow_service, "process_path", mock_process)

        scan_path_job_func(path.id)
        assert mock_process.called

    def test_encrypt_location_job(
        self, db_session, storage_location, file_inventory_factory, monkeypatch
    ):
        """Test the bulk encryption job function."""
        # Setup files in location
        storage_location.is_encrypted = False
        db_session.commit()

        inv = file_inventory_factory(
            path="/tmp/cold/f1.txt",
            storage_type=StorageType.COLD,
            is_encrypted=False,
            cold_storage_location=storage_location,
        )

        # Mock file encryption service
        from app.services.encryption_service import file_encryption_service

        monkeypatch.setattr(file_encryption_service, "encrypt_file", MagicMock())

        # Mock Path.exists and unlink
        from pathlib import Path

        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "unlink", MagicMock())

        encrypt_location_job_func(storage_location.id)

        db_session.refresh(inv)
        assert inv.is_encrypted is True
        db_session.refresh(storage_location)
        assert storage_location.encryption_status == "encrypted"

    def test_decrypt_location_job(
        self, db_session, storage_location, file_inventory_factory, monkeypatch
    ):
        """Test the bulk decryption job function."""
        storage_location.is_encrypted = True
        db_session.commit()

        inv = file_inventory_factory(
            path="/tmp/cold/f1.txt.ffenc",
            storage_type=StorageType.COLD,
            is_encrypted=True,
            cold_storage_location=storage_location,
        )

        from app.services.encryption_service import file_encryption_service

        monkeypatch.setattr(file_encryption_service, "decrypt_file", MagicMock())

        from pathlib import Path

        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "unlink", MagicMock())

        decrypt_location_job_func(storage_location.id)

        db_session.refresh(inv)
        assert inv.is_encrypted is False
        db_session.refresh(storage_location)
        assert storage_location.encryption_status == "none"

    def test_encrypt_location_job_gdrive(self, db_session, file_inventory_factory, monkeypatch):
        """GDrive files must be downloaded, encrypted, re-uploaded, and original deleted."""
        from unittest.mock import MagicMock

        from app.models import (
            ColdStorageBackendType,
            ColdStorageLocation,
            FileRecord,
            MonitoredPath,
        )

        gdrive_location = ColdStorageLocation(
            name="GDrive Enc Test",
            path="gdrive://test-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            is_encrypted=False,
        )
        db_session.add(gdrive_location)
        db_session.commit()
        db_session.refresh(gdrive_location)

        inv = file_inventory_factory(
            path="gdrive://test-folder/file-id-abc123",
            storage_type=StorageType.COLD,
            is_encrypted=False,
            cold_storage_location=gdrive_location,
        )

        from app.models import OperationType

        hot_path_obj = (
            db_session.query(MonitoredPath).filter(MonitoredPath.id == inv.path_id).first()
        )
        file_record = FileRecord(
            path_id=inv.path_id,
            original_path=hot_path_obj.source_path + "/file.txt",
            cold_storage_path="gdrive://test-folder/file-id-abc123",
            cold_storage_location_id=gdrive_location.id,
            file_size=100,
            operation_type=OperationType.MOVE,
        )
        db_session.add(file_record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "gdrive"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "gdrive://test-folder/file-id-newenc",
            None,
        )
        mock_backend.delete.return_value = (True, None)

        import app.services.cold_storage_backends as csb

        monkeypatch.setattr(csb, "get_backend", lambda loc: mock_backend)

        from app.services.encryption_service import file_encryption_service

        monkeypatch.setattr(file_encryption_service, "encrypt_file", MagicMock())

        encrypt_location_job_func(gdrive_location.id)

        db_session.refresh(inv)
        assert inv.is_encrypted is True
        assert inv.file_path == "gdrive://test-folder/file-id-newenc"
        db_session.refresh(gdrive_location)
        assert gdrive_location.encryption_status == "encrypted"

        mock_backend.download_file.assert_called_once()
        mock_backend.freeze_file.assert_called_once()
        mock_backend.delete.assert_called_once()

    def test_decrypt_location_job_gdrive(self, db_session, file_inventory_factory, monkeypatch):
        """GDrive files must be downloaded, decrypted, re-uploaded, and original deleted."""
        from unittest.mock import MagicMock

        from app.models import (
            ColdStorageBackendType,
            ColdStorageLocation,
            FileRecord,
            MonitoredPath,
        )

        gdrive_location = ColdStorageLocation(
            name="GDrive Dec Test",
            path="gdrive://test-folder-dec",
            backend_type=ColdStorageBackendType.GDRIVE,
            is_encrypted=True,
        )
        db_session.add(gdrive_location)
        db_session.commit()
        db_session.refresh(gdrive_location)

        inv = file_inventory_factory(
            path="gdrive://test-folder-dec/file-id-enc456",
            storage_type=StorageType.COLD,
            is_encrypted=True,
            cold_storage_location=gdrive_location,
        )

        from app.models import OperationType

        hot_path_obj = (
            db_session.query(MonitoredPath).filter(MonitoredPath.id == inv.path_id).first()
        )
        file_record = FileRecord(
            path_id=inv.path_id,
            original_path=hot_path_obj.source_path + "/file.txt.ffenc",
            cold_storage_path="gdrive://test-folder-dec/file-id-enc456",
            cold_storage_location_id=gdrive_location.id,
            file_size=100,
            operation_type=OperationType.MOVE,
        )
        db_session.add(file_record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "gdrive"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "gdrive://test-folder-dec/file-id-dec-new",
            None,
        )
        mock_backend.delete.return_value = (True, None)

        import app.services.cold_storage_backends as csb

        monkeypatch.setattr(csb, "get_backend", lambda loc: mock_backend)

        from app.services.encryption_service import file_encryption_service

        monkeypatch.setattr(file_encryption_service, "decrypt_file", MagicMock())

        decrypt_location_job_func(gdrive_location.id)

        db_session.refresh(inv)
        assert inv.is_encrypted is False
        assert inv.file_path == "gdrive://test-folder-dec/file-id-dec-new"
        db_session.refresh(gdrive_location)
        assert gdrive_location.encryption_status == "none"

        mock_backend.download_file.assert_called_once()
        mock_backend.freeze_file.assert_called_once()
        mock_backend.delete.assert_called_once()


class TestCheckStoragePermissionsJob:
    """Tests for check_storage_permissions_job_func."""

    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )
        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage", lambda path: (100, 50, 50)
        )

    def _make_cold_location(
        self, db_session, path: str, name: str = "Cold Storage"
    ) -> ColdStorageLocation:
        loc = ColdStorageLocation(name=name, path=path)
        db_session.add(loc)
        db_session.commit()
        db_session.refresh(loc)
        return loc

    def _make_hot_path(self, db_session, path: str, name: str = "Hot Path") -> MonitoredPath:
        from app.models import ColdStorageLocation, MonitoredPath

        cold = ColdStorageLocation(name=f"Cold for {name}", path="/tmp/cold_dummy")
        db_session.add(cold)
        db_session.flush()
        mp = MonitoredPath(name=name, source_path=path, storage_locations=[cold])
        db_session.add(mp)
        db_session.commit()
        db_session.refresh(mp)
        return mp

    def test_sets_permissions_error_on_cold_location_when_not_writable(
        self, db_session, tmp_path, monkeypatch
    ):
        """permissions_error is populated when the cold storage path is not writable."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        loc = self._make_cold_location(db_session, str(cold_path))

        # Simulate missing write permission
        monkeypatch.setattr(
            "app.services.scheduler.os.access", lambda path, mode: mode != 2
        )  # W_OK == 2

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        assert loc.permissions_error is not None
        assert "write" in loc.permissions_error

    def test_critical_low_disk_space_is_not_reported_as_write_permission_error(
        self, db_session, tmp_path, monkeypatch
    ):
        """A critically full cold drive should not be mislabeled as missing write permission."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        loc = self._make_cold_location(db_session, str(cold_path))

        # Simulate a full drive where write checks fail because no free blocks remain.
        monkeypatch.setattr("app.services.scheduler.os.access", lambda path, mode: mode != 2)
        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage",
            lambda path: (100, 96, 4),
        )

        dispatched = []
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync",
            lambda **kwargs: dispatched.append(kwargs),
        )

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        assert loc.permissions_error is None
        assert dispatched == []

    def test_caution_low_disk_space_still_reports_real_write_permission_error(
        self, db_session, tmp_path, monkeypatch
    ):
        """Only critical disk pressure suppresses write-permission classification."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        loc = self._make_cold_location(db_session, str(cold_path))

        monkeypatch.setattr("app.services.scheduler.os.access", lambda path, mode: mode != 2)
        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage",
            lambda path: (100, 85, 15),
        )

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        assert loc.permissions_error is not None
        assert "write" in loc.permissions_error

    def test_clears_permissions_error_when_permissions_restored(
        self, db_session, tmp_path, monkeypatch
    ):
        """permissions_error is cleared once the path becomes accessible again."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        loc = self._make_cold_location(db_session, str(cold_path))
        loc.permissions_error = "Missing write permission on cold storage path"
        db_session.commit()

        # All access checks pass
        monkeypatch.setattr("app.services.scheduler.os.access", lambda path, mode: True)

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        assert loc.permissions_error is None

    def test_sets_permissions_error_on_monitored_path_when_not_readable(
        self, db_session, tmp_path, monkeypatch
    ):
        """permissions_error is set on a MonitoredPath when its source is not readable."""
        from app.services.scheduler import check_storage_permissions_job_func

        hot_path = tmp_path / "hot"
        hot_path.mkdir()
        mp = self._make_hot_path(db_session, str(hot_path))

        # Simulate missing read permission only for the hot path
        def fake_access(path, mode):
            if path == str(hot_path):
                return False  # deny all
            return True

        monkeypatch.setattr("app.services.scheduler.os.access", fake_access)

        check_storage_permissions_job_func()

        db_session.refresh(mp)
        assert mp.permissions_error is not None
        assert "read" in mp.permissions_error or "write" in mp.permissions_error

    def test_sets_permissions_error_on_missing_cold_path(self, db_session, tmp_path, monkeypatch):
        """permissions_error is set when the cold storage path does not exist."""
        from app.services.scheduler import check_storage_permissions_job_func

        loc = self._make_cold_location(db_session, "/nonexistent/cold/path/xyz")

        # os.access raises FileNotFoundError for non-existent paths on some systems;
        # simulate that by having it raise directly.
        original_access = __import__("os").access

        def fake_access(path, mode):
            if "nonexistent" in str(path):
                raise FileNotFoundError(f"No such file: {path}")
            return True

        monkeypatch.setattr("app.services.scheduler.os.access", fake_access)

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        assert loc.permissions_error is not None
        assert (
            "not found" in loc.permissions_error.lower() or "nonexistent" in loc.permissions_error
        )

    def test_missing_cold_path_is_ignored_when_allow_offline_enabled(self, db_session, monkeypatch):
        """Missing removable local path does not produce permissions_error when allow_offline is enabled."""
        from app.services.scheduler import check_storage_permissions_job_func

        loc = self._make_cold_location(db_session, "/nonexistent/deep-cold/path")
        loc.allow_offline = True
        loc.local_drive_is_removable = True
        loc.local_drive_is_connected = False
        loc.permissions_error = "Path not found: /nonexistent/deep-cold/path"
        db_session.commit()

        # Ensure identity refresh does not clobber removable/offline state in this test.
        monkeypatch.setattr(
            "app.services.scheduler.update_local_drive_identity_fields", lambda _loc: None
        )

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        assert loc.permissions_error is None

    def test_no_error_for_fully_accessible_paths(self, db_session, tmp_path, monkeypatch):
        """No permissions_error is set when both hot and cold paths are fully accessible."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        hot_path = tmp_path / "hot"
        hot_path.mkdir()

        loc = self._make_cold_location(db_session, str(cold_path))
        mp = self._make_hot_path(db_session, str(hot_path))

        monkeypatch.setattr("app.services.scheduler.os.access", lambda path, mode: True)

        check_storage_permissions_job_func()

        db_session.refresh(loc)
        db_session.refresh(mp)
        assert loc.permissions_error is None
        assert mp.permissions_error is None

    def test_dispatches_notification_on_first_error(self, db_session, tmp_path, monkeypatch):
        """A STORAGE_PERMISSION_ERROR notification is dispatched the first time an error is detected."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        loc = self._make_cold_location(db_session, str(cold_path))

        monkeypatch.setattr("app.services.scheduler.os.access", lambda path, mode: mode != 2)

        dispatched = []
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync",
            lambda **kwargs: dispatched.append(kwargs),
        )

        check_storage_permissions_job_func()

        assert len(dispatched) >= 1
        from app.services.notification_events import NotificationEventType

        assert dispatched[0]["event_type"] == NotificationEventType.STORAGE_PERMISSION_ERROR

    def test_does_not_redispatch_notification_when_error_unchanged(
        self, db_session, tmp_path, monkeypatch
    ):
        """Notification is not re-dispatched on subsequent runs when the error message is unchanged."""
        from app.services.scheduler import check_storage_permissions_job_func

        cold_path = tmp_path / "cold"
        cold_path.mkdir()
        loc = self._make_cold_location(db_session, str(cold_path))

        monkeypatch.setattr("app.services.scheduler.os.access", lambda path, mode: mode != 2)

        dispatched = []
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync",
            lambda **kwargs: dispatched.append(kwargs),
        )

        # First run sets the error and dispatches
        check_storage_permissions_job_func()
        first_run_count = len(dispatched)
        assert first_run_count >= 1

        # Second run — same error — must NOT re-dispatch
        check_storage_permissions_job_func()
        assert len(dispatched) == first_run_count
