"""Extended scheduler job and SchedulerService tests."""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, PropertyMock

import pytest

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    StorageType,
)
from app.services.file_workflow_service import file_workflow_service
from app.services.scheduler import (
    SchedulerService,
    _check_and_notify_disk_space,
    check_disk_space_and_notify,
    cleanup_orphaned_fftmp_job_func,
    disk_space_monitoring_job_func,
    recall_location_job_func,
    scan_path_job_func,
    sync_p2p_manifests_job_func,
)


@pytest.mark.unit
class TestSchedulerJobsExtended:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_sync_p2p_manifests_job_func(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_sync = MagicMock(return_value=3)
        monkeypatch.setattr("app.services.scheduler.p2p_service.sync_all_peer_manifests", mock_sync)

        sync_p2p_manifests_job_func()
        mock_sync.assert_called_once()

    def test_sync_p2p_manifests_job_func_handles_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.scheduler.p2p_service.sync_all_peer_manifests",
            MagicMock(side_effect=RuntimeError("network")),
        )
        sync_p2p_manifests_job_func()

    def test_disk_space_monitoring_local_location(
        self, db_session, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cold_path = tmp_path / "cold_disk"
        cold_path.mkdir()
        loc = ColdStorageLocation(name="Disk Loc", path=str(cold_path))
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage",
            lambda _p: (1000, 200, 800),
        )
        monkeypatch.setattr(
            "app.services.scheduler._check_and_notify_disk_space",
            MagicMock(return_value=(None, 80.0)),
        )

        disk_space_monitoring_job_func()

    def test_disk_space_monitoring_skips_non_local(
        self, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models import ColdStorageBackendType

        loc = ColdStorageLocation(
            name="S3 Loc",
            path="s3://bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        check_mock = MagicMock(return_value=(None, None))
        monkeypatch.setattr("app.services.scheduler._check_and_notify_disk_space", check_mock)

        disk_space_monitoring_job_func()
        check_mock.assert_called_once()

    def test_disk_space_monitoring_file_not_found(
        self, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loc = ColdStorageLocation(name="Missing", path="/missing/cold/path")
        db_session.add(loc)
        db_session.commit()

        def raise_not_found(*_a, **_k):
            raise FileNotFoundError("missing")

        monkeypatch.setattr("app.services.scheduler._check_and_notify_disk_space", raise_not_found)
        disk_space_monitoring_job_func()

    def test_cleanup_orphaned_fftmp_deletes_old_files(
        self, db_session, monitored_path_factory, tmp_path
    ) -> None:
        hot_dir = tmp_path / "hot_fftmp"
        hot_dir.mkdir()
        monitored_path_factory("FFTMP Path", str(hot_dir))

        old_file = hot_dir / "old.fftmp"
        old_file.write_text("stale")
        old_ts = (datetime.now() - timedelta(days=8)).timestamp()
        os.utime(old_file, (old_ts, old_ts))

        recent_file = hot_dir / "recent.fftmp"
        recent_file.write_text("recent")
        recent_ts = (datetime.now() - timedelta(hours=12)).timestamp()
        os.utime(recent_file, (recent_ts, recent_ts))

        cleanup_orphaned_fftmp_job_func()

        assert not old_file.exists()
        assert recent_file.exists()

    def test_cleanup_orphaned_fftmp_warning_for_day_old(
        self, db_session, monitored_path_factory, tmp_path
    ) -> None:
        hot_dir = tmp_path / "warn_fftmp"
        hot_dir.mkdir()
        monitored_path_factory("Warn Path", str(hot_dir))

        stuck = hot_dir / "stuck.fftmp"
        stuck.write_text("stuck")
        ts = (datetime.now() - timedelta(days=2)).timestamp()
        os.utime(stuck, (ts, ts))

        cleanup_orphaned_fftmp_job_func()
        assert stuck.exists()

    def test_recall_location_not_found(self) -> None:
        recall_location_job_func(99999)

    def test_recall_location_success(
        self,
        db_session,
        storage_location,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        hot = monitored_path_factory("Recall Hot", "/tmp/recall_hot")
        inv = file_inventory_factory(
            path="/tmp/cold/recall.bin",
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
            status=FileStatus.ACTIVE,
        )
        inv.cold_storage_location_id = storage_location.id
        db_session.commit()

        record = FileRecord(
            path_id=hot.id,
            original_path=f"{hot.source_path}/recall.bin",
            cold_storage_path=inv.file_path,
            cold_storage_location_id=storage_location.id,
            file_size=50,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        thaw_mock = MagicMock(return_value=(True, None))
        monkeypatch.setattr("app.services.file_thawer.FileThawer.thaw_file", thaw_mock)

        recall_location_job_func(storage_location.id)
        thaw_mock.assert_called_once()

    def test_recall_location_skips_migrating(
        self, db_session, storage_location, file_inventory_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv = file_inventory_factory(
            path="/tmp/cold/migrating.bin",
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
            status=FileStatus.MIGRATING,
        )
        inv.cold_storage_location_id = storage_location.id
        db_session.commit()

        thaw_mock = MagicMock()
        monkeypatch.setattr("app.services.file_thawer.FileThawer.thaw_file", thaw_mock)

        recall_location_job_func(storage_location.id)
        thaw_mock.assert_not_called()

    def test_cleanup_old_stats_job_func(self, db_session, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.stats_cleanup import StatsCleanupService, cleanup_old_stats_job_func

        service = StatsCleanupService()
        cleanup_mock = MagicMock(return_value={"records_deleted": 1})
        temp_mock = MagicMock(return_value={"files_deleted": 0})
        monkeypatch.setattr(service, "cleanup_old_records", cleanup_mock)
        monkeypatch.setattr(service, "cleanup_orphaned_temp_files", temp_mock)
        monkeypatch.setattr(
            "app.services.stats_cleanup.StatsCleanupService", MagicMock(return_value=service)
        )
        monkeypatch.setattr(
            "app.services.stats_cleanup.SessionLocal", MagicMock(return_value=db_session)
        )
        monkeypatch.setattr(db_session, "close", lambda: None)

        cleanup_old_stats_job_func()
        cleanup_mock.assert_called_once()
        temp_mock.assert_called_once()


@pytest.mark.unit
class TestSchedulerServiceLifecycle:
    def test_start_stop_and_add_path_job(self, db_session, monitored_path_factory, monkeypatch):
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        type(mock_scheduler).running = PropertyMock(side_effect=[False, True, True, False])

        service = SchedulerService.__new__(SchedulerService)
        service.scheduler = mock_scheduler

        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )
        monkeypatch.setattr(service, "_load_existing_jobs", MagicMock())
        monkeypatch.setattr(service, "_add_stats_cleanup_job", MagicMock())
        monkeypatch.setattr(service, "_add_disk_space_monitoring_job", MagicMock())
        monkeypatch.setattr(service, "_add_storage_permissions_job", MagicMock())
        monkeypatch.setattr(service, "_add_fftmp_cleanup_job", MagicMock())
        monkeypatch.setattr(service, "_add_p2p_manifest_sync_job", MagicMock())
        monkeypatch.setattr("app.services.scheduler.time.sleep", lambda _s: None)

        service.start()
        mock_scheduler.start.assert_called_once()

        path = monitored_path_factory("Scheduled", "/tmp/scheduled")
        mock_scheduler.get_job.return_value = MagicMock()
        service.add_path_job(path)
        mock_scheduler.remove_job.assert_called()
        mock_scheduler.add_job.assert_called()

        type(mock_scheduler).running = PropertyMock(return_value=True)
        service.stop()
        mock_scheduler.shutdown.assert_called_with(wait=False)

    def test_add_path_job_when_scheduler_not_running(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        service = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        service.scheduler = mock_scheduler

        path = monitored_path_factory("Disabled Scheduler", "/tmp/x")
        service.add_path_job(path)
        mock_scheduler.add_job.assert_not_called()


@pytest.mark.unit
class TestSchedulerServiceMethodsExtended:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_trigger_and_remove_jobs(self, db_session, monitored_path_factory):
        service = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        service.scheduler = mock_scheduler

        path = monitored_path_factory("TriggerPath", "/tmp/trigger_hot")
        service.trigger_scan(path.id)
        service.trigger_encryption_job(1)
        service.trigger_decryption_job(2)
        service.trigger_recall_job(3)
        assert mock_scheduler.add_job.call_count == 4

        mock_scheduler.get_job.return_value = MagicMock()
        service.remove_path_job(path.id)
        mock_scheduler.remove_job.assert_called()

    def test_add_internal_jobs_when_scheduler_running(self, monkeypatch):
        service = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler.get_job.return_value = None
        service.scheduler = mock_scheduler

        service._add_stats_cleanup_job()
        service._add_disk_space_monitoring_job()
        service._add_storage_permissions_job()
        service._add_fftmp_cleanup_job()
        service._add_p2p_manifest_sync_job()
        assert mock_scheduler.add_job.call_count == 5

    def test_add_internal_jobs_skip_when_not_running(self):
        service = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        service.scheduler = mock_scheduler

        service._add_stats_cleanup_job()
        service._add_disk_space_monitoring_job()
        mock_scheduler.add_job.assert_not_called()

    def test_load_existing_jobs(self, db_session, monitored_path_factory, monkeypatch):
        service = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        service.scheduler = mock_scheduler
        monitored_path_factory("LoadJobPath", "/tmp/load_job_hot")
        monkeypatch.setattr(
            service, "add_path_job", MagicMock(side_effect=[None, RuntimeError("x")])
        )

        service._load_existing_jobs()
        assert service.add_path_job.call_count >= 1


@pytest.mark.unit
class TestSchedulerDiskSpaceHelpers:
    def test_check_and_notify_critical_and_caution(self, db_session, tmp_path, monkeypatch):
        loc = ColdStorageLocation(
            name="Critical Disk",
            path=str(tmp_path),
            caution_threshold_percent=20,
            critical_threshold_percent=10,
        )
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage",
            lambda _p: (1000, 950, 50),
        )
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )

        level, free_pct = _check_and_notify_disk_space(loc, db_session)
        assert level == "critical"
        assert free_pct == 5.0
        assert dispatch.call_count == 1

        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage",
            lambda _p: (1000, 850, 150),
        )
        dispatch.reset_mock()
        level, free_pct = _check_and_notify_disk_space(loc, db_session)
        assert level == "caution"
        assert free_pct == 15.0

    def test_check_and_notify_dispatch_failure_still_returns_level(
        self, db_session, tmp_path, monkeypatch
    ):
        loc = ColdStorageLocation(
            name="Dispatch Fail Disk",
            path=str(tmp_path),
            caution_threshold_percent=20,
            critical_threshold_percent=10,
        )
        db_session.add(loc)
        db_session.commit()
        monkeypatch.setattr(
            "app.services.scheduler.shutil.disk_usage",
            lambda _p: (1000, 950, 50),
        )
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync",
            MagicMock(side_effect=RuntimeError("notify down")),
        )
        level, _ = _check_and_notify_disk_space(loc, db_session)
        assert level == "critical"

    def test_check_disk_space_and_notify_handles_generic_error(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        path = monitored_path_factory("DiskGenericErr", "/tmp/disk_generic_err_hot")
        monkeypatch.setattr(
            "app.services.scheduler._check_and_notify_disk_space",
            MagicMock(side_effect=RuntimeError("unexpected")),
        )
        check_disk_space_and_notify(path, db_session)

    def test_check_disk_space_and_notify_handles_missing_path(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        path = monitored_path_factory("DiskNotify", "/tmp/disk_notify_hot")
        path.storage_locations[0].path = "/missing/disk/path"
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_and_notify_disk_space",
            MagicMock(side_effect=FileNotFoundError("missing")),
        )
        check_disk_space_and_notify(path, db_session)


@pytest.mark.unit
class TestScanPathJobFuncExtended:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_scan_path_job_sends_notifications(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        path = monitored_path_factory("NotifyScan", "/tmp/notify_scan_hot")
        monkeypatch.setattr(
            file_workflow_service,
            "process_path",
            MagicMock(
                return_value={
                    "files_moved": 2,
                    "total_scanned": 10,
                    "files_skipped": 1,
                    "bytes_saved": 100,
                    "errors": ["minor issue"],
                }
            ),
        )
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )
        monkeypatch.setattr("app.services.scheduler.check_disk_space_and_notify", MagicMock())
        monkeypatch.setattr("app.services.scheduler.shutil.disk_usage", lambda _p: (100, 20, 80))

        scan_path_job_func(path.id)
        assert dispatch.call_count >= 2

    def test_scan_path_job_fatal_error(self, db_session, monitored_path_factory, monkeypatch):
        path = monitored_path_factory("FatalScan", "/tmp/fatal_scan_hot")
        monkeypatch.setattr(
            file_workflow_service, "process_path", MagicMock(side_effect=RuntimeError("fatal"))
        )
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )

        scan_path_job_func(path.id)
        dispatch.assert_called()

    def test_disk_space_monitoring_caution_branch(self, db_session, tmp_path, monkeypatch):
        loc = ColdStorageLocation(
            name="Caution Disk",
            path=str(tmp_path),
            caution_threshold_percent=30,
            critical_threshold_percent=5,
        )
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_and_notify_disk_space",
            MagicMock(return_value=("caution", 25.0)),
        )
        disk_space_monitoring_job_func()


@pytest.mark.unit
class TestSchedulerEncryptionAndPermissions:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_encrypt_location_job_local_success(
        self, db_session, tmp_path, file_inventory_factory, monkeypatch
    ):
        from app.models import EncryptionStatus
        from app.services.scheduler import encrypt_location_job_func

        cold_dir = tmp_path / "enc_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Enc Loc", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        enc_file = cold_dir / "secret.bin"
        enc_file.write_bytes(b"plain-secret")
        inv = file_inventory_factory(path=str(enc_file), storage_type=StorageType.COLD)
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        db_session.commit()

        encrypt_mock = MagicMock(side_effect=lambda _db, src, dest: dest.write_bytes(b"encrypted"))
        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", encrypt_mock
        )

        encrypt_location_job_func(loc.id)

        db_session.expire_all()
        refreshed = db_session.get(FileInventory, inv.id)
        assert refreshed.is_encrypted is True
        assert refreshed.file_path.endswith(".ffenc")
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.ENCRYPTED

    def test_encrypt_location_missing_location(self):
        from app.services.scheduler import encrypt_location_job_func

        encrypt_location_job_func(424242)

    def test_decrypt_location_job_local_success(
        self, db_session, tmp_path, file_inventory_factory, monkeypatch
    ):
        from app.models import EncryptionStatus
        from app.services.scheduler import decrypt_location_job_func

        cold_dir = tmp_path / "dec_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Dec Loc", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        enc_file = cold_dir / "secret.bin.ffenc"
        enc_file.write_bytes(b"cipher")
        inv = file_inventory_factory(path=str(enc_file), storage_type=StorageType.COLD)
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = True
        db_session.commit()

        decrypt_mock = MagicMock(side_effect=lambda _db, src, dest: dest.write_bytes(b"plain"))
        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file", decrypt_mock
        )

        decrypt_location_job_func(loc.id)

        db_session.expire_all()
        refreshed = db_session.get(FileInventory, inv.id)
        assert refreshed.is_encrypted is False
        assert refreshed.file_path.endswith("secret.bin")
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.NONE

    def test_encrypt_location_job_continues_after_file_error(
        self, db_session, tmp_path, file_inventory_factory, monkeypatch
    ):
        from app.services.scheduler import encrypt_location_job_func

        cold_dir = tmp_path / "enc_error_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Enc Error Loc", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        good = cold_dir / "good.bin"
        good.write_bytes(b"ok")
        bad = cold_dir / "bad.bin"
        bad.write_bytes(b"bad")
        good_inv = file_inventory_factory(path=str(good), storage_type=StorageType.COLD)
        bad_inv = file_inventory_factory(path=str(bad), storage_type=StorageType.COLD)
        good_inv.cold_storage_location_id = loc.id
        bad_inv.cold_storage_location_id = loc.id
        db_session.commit()

        def _encrypt(_db, src, dest):
            if src.name == "bad.bin":
                raise RuntimeError("encrypt failed")
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )
        encrypt_location_job_func(loc.id)
        db_session.refresh(good_inv)
        assert good_inv.is_encrypted is True

    def test_encrypt_location_job_skips_missing_local_file(
        self, db_session, tmp_path, file_inventory_factory, monkeypatch
    ):
        from app.models import EncryptionStatus
        from app.services.scheduler import encrypt_location_job_func

        cold_dir = tmp_path / "enc_missing_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Enc Missing", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path=str(cold_dir / "missing.bin"),
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        db_session.commit()

        encrypt_location_job_func(loc.id)
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.ENCRYPTED

    def test_decrypt_location_job_remote_backend(
        self,
        db_session,
        tmp_path,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.models import ColdStorageBackendType, EncryptionStatus, FileRecord
        from app.services.scheduler import decrypt_location_job_func

        path = monitored_path_factory("Dec Remote", str(tmp_path / "dec_hot"))
        loc = ColdStorageLocation(
            name="S3 Dec",
            path="s3://dec-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://dec-bucket/secret.bin.ffenc",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = True
        record = FileRecord(
            path_id=path.id,
            original_path=str(tmp_path / "dec_hot" / "secret.bin"),
            cold_storage_path="s3://dec-bucket/secret.bin.ffenc",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "s3://dec-bucket/secret.bin",
            None,
        )
        mock_backend.delete.return_value = (True, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _decrypt(_db, _src, dest):
            dest.write_bytes(b"plain")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file", _decrypt
        )

        decrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is False
        assert inv.file_path == "s3://dec-bucket/secret.bin"
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.NONE

    def test_encrypt_location_job_remote_download_failure(
        self, db_session, file_inventory_factory, monkeypatch
    ):
        from app.models import ColdStorageBackendType, EncryptionStatus
        from app.services.scheduler import encrypt_location_job_func

        loc = ColdStorageLocation(
            name="S3 Enc Fail DL",
            path="s3://enc-fail",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://enc-fail/file.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (False, "download failed")
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        encrypt_location_job_func(loc.id)
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.ENCRYPTED
        db_session.refresh(inv)
        assert inv.is_encrypted is False

    def test_decrypt_location_missing_location(self):
        from app.services.scheduler import decrypt_location_job_func

        decrypt_location_job_func(424242)

    def test_decrypt_location_job_skips_missing_local_file(
        self, db_session, tmp_path, file_inventory_factory
    ):
        from app.models import EncryptionStatus
        from app.services.scheduler import decrypt_location_job_func

        cold_dir = tmp_path / "dec_missing_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Dec Missing", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path=str(cold_dir / "missing.ffenc"),
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = True
        db_session.commit()

        decrypt_location_job_func(loc.id)
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.NONE

    def test_encrypt_location_job_remote_without_file_record(
        self, db_session, file_inventory_factory, monkeypatch
    ):
        from app.models import ColdStorageBackendType, EncryptionStatus
        from app.services.scheduler import encrypt_location_job_func

        loc = ColdStorageLocation(
            name="S3 Enc No Record",
            path="s3://enc-no-record",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://enc-no-record/data.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "s3://enc-no-record/ffenc_data.bin.ffenc",
            None,
        )
        mock_backend.delete.return_value = (True, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _encrypt(_db, _src, dest):
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )

        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is True
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.ENCRYPTED

    def test_decrypt_location_job_remote_download_failure(
        self, db_session, file_inventory_factory, monkeypatch
    ):
        from app.models import ColdStorageBackendType, EncryptionStatus
        from app.services.scheduler import decrypt_location_job_func

        loc = ColdStorageLocation(
            name="S3 Dec Fail DL",
            path="s3://dec-fail",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://dec-fail/file.bin.ffenc",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = True
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (False, "download failed")
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        decrypt_location_job_func(loc.id)
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.NONE
        db_session.refresh(inv)
        assert inv.is_encrypted is True

    def test_encrypt_location_job_remote_upload_failure(
        self, db_session, file_inventory_factory, monkeypatch
    ):
        from app.models import ColdStorageBackendType, EncryptionStatus
        from app.services.scheduler import encrypt_location_job_func

        loc = ColdStorageLocation(
            name="S3 Enc Fail UL",
            path="s3://enc-fail-ul",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://enc-fail-ul/file.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (False, "upload failed", None, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _encrypt(_db, _src, dest):
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )

        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is False
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.ENCRYPTED

    def test_decrypt_location_job_remote_upload_failure(
        self, db_session, file_inventory_factory, monkeypatch
    ):
        from app.models import ColdStorageBackendType, EncryptionStatus
        from app.services.scheduler import decrypt_location_job_func

        loc = ColdStorageLocation(
            name="S3 Dec Fail UL",
            path="s3://dec-fail-ul",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://dec-fail-ul/file.bin.ffenc",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = True
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (False, "upload failed", None, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _decrypt(_db, _src, dest):
            dest.write_bytes(b"plain")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file", _decrypt
        )

        decrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is True
        db_session.refresh(loc)
        assert loc.encryption_status == EncryptionStatus.NONE

    def test_check_path_permissions_helpers(self, tmp_path):
        from app.services.scheduler import _check_path_permissions, _local_disk_space_level

        readable = tmp_path / "readable"
        readable.mkdir()
        assert _check_path_permissions(str(readable)) == []

        with pytest.raises(FileNotFoundError):
            _check_path_permissions("/path/does/not/exist/xyz")

        from app.models import ColdStorageBackendType

        loc = ColdStorageLocation(
            name="Disk Level",
            path=str(readable),
            backend_type=ColdStorageBackendType.LOCAL,
            caution_threshold_percent=50,
            critical_threshold_percent=10,
        )
        level, pct = _local_disk_space_level(loc)
        assert level in (None, "caution", "critical")
        assert pct is None or pct >= 0

    def test_storage_permissions_job_local_missing_write(self, db_session, tmp_path, monkeypatch):
        from app.services.scheduler import check_storage_permissions_job_func

        cold = tmp_path / "perm_cold"
        cold.mkdir()
        loc = ColdStorageLocation(name="Perm Cold", path=str(cold))
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions", MagicMock(return_value=["write"])
        )
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )

        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(ColdStorageLocation, loc.id)
        assert refreshed.permissions_error is not None
        dispatch.assert_called()

    def test_storage_permissions_remote_validation_failure(self, db_session, monkeypatch):
        from app.models import ColdStorageBackendType
        from app.services.scheduler import check_storage_permissions_job_func

        loc = ColdStorageLocation(
            name="Remote Perm",
            path="s3://bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.capabilities.return_value.supports_local_path_stats = False
        mock_backend.validate_location.return_value = (False, "bad credentials")
        monkeypatch.setattr("app.services.scheduler.get_backend", lambda _loc: mock_backend)

        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(ColdStorageLocation, loc.id)
        assert "bad credentials" in (refreshed.permissions_error or "")

    def test_scan_path_job_skips_disabled_path(self, db_session, monitored_path_factory):
        from app.services.scheduler import scan_path_job_func

        path = monitored_path_factory("Disabled", "/tmp/disabled_scan")
        path.enabled = False
        db_session.commit()
        scan_path_job_func(path.id)

    def test_encrypt_location_job_remote_backend(
        self,
        db_session,
        tmp_path,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.models import ColdStorageBackendType, FileRecord
        from app.services.scheduler import encrypt_location_job_func

        path = monitored_path_factory("Enc Remote", str(tmp_path / "enc_hot"))
        loc = ColdStorageLocation(
            name="S3 Enc",
            path="s3://enc-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()

        inv = file_inventory_factory(
            path="s3://enc-bucket/file.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        record = FileRecord(
            path_id=path.id,
            original_path=str(tmp_path / "enc_hot" / "file.bin"),
            cold_storage_path="s3://enc-bucket/file.bin",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "s3://enc-bucket/ffenc_file.bin.ffenc",
            None,
        )
        mock_backend.delete.return_value = (True, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _encrypt(_db, _src, dest):
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )

        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is True

    def test_storage_permissions_hot_path_missing(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        from app.services.scheduler import check_storage_permissions_job_func

        hot_dir = tmp_path / "hot_perm_check"
        hot_dir.mkdir()
        path = monitored_path_factory("Hot Perm", str(hot_dir))

        def _check_permissions(path_str: str):
            if str(hot_dir) in path_str:
                return ["read"]
            return []

        monkeypatch.setattr("app.services.scheduler._check_path_permissions", _check_permissions)
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )

        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(MonitoredPath, path.id)
        assert refreshed.permissions_error is not None

    def test_storage_permissions_offline_removable_drive(self, db_session, tmp_path, monkeypatch):
        from app.models import ColdStorageBackendType
        from app.services.scheduler import check_storage_permissions_job_func

        loc = ColdStorageLocation(
            name="Offline Drive",
            path=str(tmp_path / "offline_cold"),
            backend_type=ColdStorageBackendType.LOCAL,
            allow_offline=True,
            local_drive_is_removable=True,
            local_drive_is_connected=False,
        )
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions",
            MagicMock(side_effect=FileNotFoundError("missing")),
        )
        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(ColdStorageLocation, loc.id)
        assert refreshed.permissions_error is None

    def test_storage_permissions_hot_path_not_found_via_job(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        from app.services.scheduler import check_storage_permissions_job_func

        path = monitored_path_factory("Missing Hot Job", "/tmp/missing_hot_job_xyz")
        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions",
            MagicMock(side_effect=FileNotFoundError("missing")),
        )
        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(MonitoredPath, path.id)
        assert refreshed.permissions_error is not None


@pytest.mark.unit
class TestSchedulerRecallAndFftmp:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_recall_location_job_thaws_files(
        self, db_session, tmp_path, file_inventory_factory, monkeypatch
    ):
        cold_dir = tmp_path / "recall_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Recall Loc", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        cold_file = cold_dir / "cold.dat"
        cold_file.write_bytes(b"cold")
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD)
        inv.cold_storage_location_id = loc.id
        inv.status = FileStatus.ACTIVE
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(cold_file),
            cold_storage_path=str(cold_file),
            cold_storage_location_id=loc.id,
            file_size=4,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        thaw_mock = MagicMock(return_value=(True, None))
        monkeypatch.setattr("app.services.file_thawer.FileThawer.thaw_file", thaw_mock)

        recall_location_job_func(loc.id)
        thaw_mock.assert_called()

    def test_storage_permissions_offline_removable_drive(self, db_session, tmp_path, monkeypatch):
        from app.models import ColdStorageBackendType
        from app.services.scheduler import check_storage_permissions_job_func

        cold = tmp_path / "offline_cold"
        loc = ColdStorageLocation(
            name="Offline Drive",
            path=str(cold),
            backend_type=ColdStorageBackendType.LOCAL,
            allow_offline=True,
            local_drive_is_removable=True,
            local_drive_is_connected=False,
        )
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions",
            MagicMock(side_effect=FileNotFoundError("missing")),
        )
        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(ColdStorageLocation, loc.id)
        assert refreshed.permissions_error is None

    def test_storage_permissions_hot_path_not_found(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        from app.services.scheduler import check_storage_permissions_job_func

        path = monitored_path_factory("Missing Hot", "/tmp/missing_hot_path_xyz")
        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions",
            MagicMock(side_effect=FileNotFoundError("missing")),
        )
        check_storage_permissions_job_func()
        db_session.expire_all()
        refreshed = db_session.get(MonitoredPath, path.id)
        assert refreshed.permissions_error is not None

    def test_recall_location_missing(self):
        recall_location_job_func(999999)

    def test_cleanup_orphaned_fftmp_deletes_old_files(self, db_session, tmp_path, monkeypatch):
        from datetime import datetime, timedelta

        hot = tmp_path / "fftmp_hot"
        hot.mkdir()
        mp = MonitoredPath(name="FFTMP Path", source_path=str(hot), operation_type="move")
        db_session.add(mp)
        db_session.commit()

        old_fftmp = hot / "stale.fftmp"
        old_fftmp.write_bytes(b"x")
        old_ts = (datetime.now() - timedelta(days=10)).timestamp()
        import os

        os.utime(old_fftmp, (old_ts, old_ts))

        cleanup_orphaned_fftmp_job_func()
        assert not old_fftmp.exists()

    def test_scheduler_service_start_stop_and_jobs(self, monkeypatch, db_session):
        service = SchedulerService()
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        service.scheduler = mock_scheduler
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )
        monkeypatch.setattr(service, "_load_existing_jobs", MagicMock())
        monkeypatch.setattr(service, "_add_stats_cleanup_job", MagicMock())
        monkeypatch.setattr(service, "_add_disk_space_monitoring_job", MagicMock())
        monkeypatch.setattr(service, "_add_storage_permissions_job", MagicMock())
        monkeypatch.setattr(service, "_add_fftmp_cleanup_job", MagicMock())
        monkeypatch.setattr(service, "_add_p2p_manifest_sync_job", MagicMock())
        monkeypatch.setattr("app.services.scheduler.time.sleep", lambda _s: None)
        service.start()
        mock_scheduler.start.assert_called_once()
        mock_scheduler.running = True
        service.stop()
        mock_scheduler.shutdown.assert_called_once()


@pytest.mark.unit
class TestSchedulerStartFailureAndNonSqlite:
    def test_scheduler_start_failure_shuts_down(self, monkeypatch):
        service = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        service.scheduler = mock_scheduler
        monkeypatch.setattr(
            service, "_load_existing_jobs", MagicMock(side_effect=RuntimeError("load"))
        )
        monkeypatch.setattr(service, "_add_stats_cleanup_job", MagicMock())
        monkeypatch.setattr(service, "_add_disk_space_monitoring_job", MagicMock())
        monkeypatch.setattr(service, "_add_storage_permissions_job", MagicMock())
        monkeypatch.setattr(service, "_add_fftmp_cleanup_job", MagicMock())
        monkeypatch.setattr(service, "_add_p2p_manifest_sync_job", MagicMock())
        monkeypatch.setattr("app.services.scheduler.time.sleep", lambda _s: None)

        mock_scheduler.start.side_effect = lambda: setattr(mock_scheduler, "running", True)

        with pytest.raises(RuntimeError, match="load"):
            service.start()
        mock_scheduler.shutdown.assert_called_with(wait=False)


@pytest.mark.unit
class TestScanPathJobNotificationBranches:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_scan_path_job_zero_files_moved(self, db_session, monitored_path_factory, monkeypatch):
        path = monitored_path_factory("ZeroMoved", "/tmp/zero_moved_hot")
        monkeypatch.setattr(
            file_workflow_service,
            "process_path",
            MagicMock(
                return_value={
                    "files_moved": 0,
                    "total_scanned": 0,
                    "files_skipped": 0,
                    "bytes_saved": 0,
                    "errors": [],
                }
            ),
        )
        monkeypatch.setattr("app.services.scheduler.check_disk_space_and_notify", MagicMock())
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )
        scan_path_job_func(path.id)
        assert dispatch.call_count >= 1

    def test_recall_location_thaw_failure(
        self, db_session, tmp_path, file_inventory_factory, monkeypatch
    ):
        cold_dir = tmp_path / "recall_fail_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Recall Fail", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()
        cold_file = cold_dir / "fail.dat"
        cold_file.write_bytes(b"x")
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD)
        inv.cold_storage_location_id = loc.id
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(cold_file),
            cold_storage_path=str(cold_file),
            cold_storage_location_id=loc.id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()
        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer.thaw_file",
            MagicMock(return_value=(False, "thaw failed")),
        )
        recall_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.status == FileStatus.ACTIVE


@pytest.mark.unit
class TestEncryptDecryptRemoteJobBranches:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.services.scheduler.SchedulerSessionLocal", MagicMock(return_value=db_session)
        )

    def test_encrypt_remote_download_failure_skips_file(
        self,
        db_session,
        tmp_path,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.models import ColdStorageBackendType
        from app.services.scheduler import encrypt_location_job_func

        hot = monitored_path_factory("Enc DL Fail", str(tmp_path / "enc_dl_hot"))
        loc = ColdStorageLocation(
            name="Remote Enc DL",
            path="s3://enc-dl-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()
        inv = file_inventory_factory(
            path="s3://enc-dl-bucket/file.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        record = FileRecord(
            path_id=hot.id,
            original_path=str(tmp_path / "enc_dl_hot" / "file.bin"),
            cold_storage_path="s3://enc-dl-bucket/file.bin",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (False, "download failed")
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )
        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is False

    def test_encrypt_remote_upload_failure_skips_file(
        self,
        db_session,
        tmp_path,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.models import ColdStorageBackendType
        from app.services.scheduler import encrypt_location_job_func

        hot = monitored_path_factory("Enc UL Fail", str(tmp_path / "enc_ul_hot"))
        loc = ColdStorageLocation(
            name="Remote Enc UL",
            path="s3://enc-ul-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()
        inv = file_inventory_factory(
            path="s3://enc-ul-bucket/file2.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        record = FileRecord(
            path_id=hot.id,
            original_path=str(tmp_path / "enc_ul_hot" / "file2.bin"),
            cold_storage_path="s3://enc-ul-bucket/file2.bin",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (False, "upload failed", None, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _encrypt(_db, _src, dest):
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )
        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is False

    def test_encrypt_remote_delete_original_failure_still_encrypts(
        self,
        db_session,
        tmp_path,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.models import ColdStorageBackendType, FileRecord
        from app.services.scheduler import encrypt_location_job_func

        hot = monitored_path_factory("Enc Del Fail", str(tmp_path / "enc_del_hot"))
        loc = ColdStorageLocation(
            name="Remote Enc Del",
            path="s3://enc-del-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()
        inv = file_inventory_factory(
            path="s3://enc-del-bucket/file3.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        record = FileRecord(
            path_id=hot.id,
            original_path=str(tmp_path / "enc_del_hot" / "file3.bin"),
            cold_storage_path="s3://enc-del-bucket/file3.bin",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "s3://enc-del-bucket/ffenc_1.ffenc",
            None,
        )
        mock_backend.delete.return_value = (False, "delete denied")
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _encrypt(_db, _src, dest):
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )
        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is True
        mock_backend.delete.assert_called_once()

    def test_encrypt_remote_relative_path_outside_source(
        self,
        db_session,
        tmp_path,
        file_inventory_factory,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.models import ColdStorageBackendType, FileRecord
        from app.services.scheduler import encrypt_location_job_func

        hot = monitored_path_factory("Enc Rel", str(tmp_path / "enc_rel_hot"))
        loc = ColdStorageLocation(
            name="Remote Enc Rel",
            path="s3://enc-rel-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.commit()
        inv = file_inventory_factory(
            path="s3://enc-rel-bucket/outside.bin",
            storage_type=StorageType.COLD,
        )
        inv.cold_storage_location_id = loc.id
        inv.is_encrypted = False
        record = FileRecord(
            path_id=hot.id,
            original_path="/other/root/outside.bin",
            cold_storage_path="s3://enc-rel-bucket/outside.bin",
            file_size=10,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.download_file.return_value = (True, None)
        mock_backend.freeze_file.return_value = (
            True,
            None,
            "s3://enc-rel-bucket/ffenc_out.ffenc",
            None,
        )
        mock_backend.delete.return_value = (True, None)
        monkeypatch.setattr(
            "app.services.cold_storage_backends.get_backend", lambda _loc: mock_backend
        )

        def _encrypt(_db, _src, dest):
            dest.write_bytes(b"enc")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file", _encrypt
        )
        encrypt_location_job_func(loc.id)
        db_session.refresh(inv)
        assert inv.is_encrypted is True

    def test_scan_path_job_scan_error_notification_failure(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        path = monitored_path_factory("ScanErrNotify", "/tmp/scan_err_notify_hot")
        monkeypatch.setattr(
            file_workflow_service,
            "process_path",
            MagicMock(
                return_value={
                    "files_moved": 0,
                    "total_scanned": 1,
                    "files_skipped": 0,
                    "bytes_saved": 0,
                    "errors": ["minor"],
                }
            ),
        )
        dispatch = MagicMock(side_effect=[RuntimeError("notify fail"), None, None])
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )
        monkeypatch.setattr("app.services.scheduler.check_disk_space_and_notify", MagicMock())
        scan_path_job_func(path.id)
        assert dispatch.call_count >= 1
        assert db_session.get(MonitoredPath, path.id) is not None

    def test_scan_path_job_files_moved_includes_cold_stats(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot = tmp_path / "scan_moved_hot"
        hot.mkdir()
        cold = tmp_path / "scan_moved_cold"
        cold.mkdir()
        path = monitored_path_factory("ScanMoved", str(hot))
        loc = ColdStorageLocation(name="ScanMovedCold", path=str(cold))
        db_session.add(loc)
        db_session.flush()
        mp = db_session.get(MonitoredPath, path.id)
        mp.storage_locations.append(loc)
        db_session.commit()

        monkeypatch.setattr(
            file_workflow_service,
            "process_path",
            MagicMock(
                return_value={
                    "files_moved": 2,
                    "total_scanned": 2,
                    "files_skipped": 0,
                    "bytes_saved": 100,
                    "errors": [],
                }
            ),
        )
        monkeypatch.setattr("app.services.scheduler.check_disk_space_and_notify", MagicMock())
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )
        scan_path_job_func(path.id)
        assert dispatch.call_count >= 1

    def test_scan_path_job_fatal_exception_dispatches_error(
        self, db_session, monitored_path_factory, monkeypatch
    ):
        path = monitored_path_factory("ScanFatal", "/tmp/scan_fatal_hot")
        monkeypatch.setattr(
            file_workflow_service, "process_path", MagicMock(side_effect=RuntimeError("fatal scan"))
        )
        dispatch = MagicMock()
        monkeypatch.setattr(
            "app.services.scheduler.notification_service.dispatch_event_sync", dispatch
        )
        scan_path_job_func(path.id)
        assert dispatch.call_count >= 1

    def test_storage_permissions_critical_disk_write_only(self, db_session, tmp_path, monkeypatch):
        from app.services.scheduler import check_storage_permissions_job_func

        cold = tmp_path / "crit_perm_cold"
        cold.mkdir()
        loc = ColdStorageLocation(
            name="Crit Perm",
            path=str(cold),
            caution_threshold_percent=50,
            critical_threshold_percent=90,
        )
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions", MagicMock(return_value=["write"])
        )
        monkeypatch.setattr(
            "app.services.scheduler._local_disk_space_level",
            MagicMock(return_value=("critical", 5.0)),
        )
        check_storage_permissions_job_func()
        db_session.refresh(loc)
        assert loc.permissions_error is None

    def test_storage_permissions_restored_clears_error(self, db_session, tmp_path, monkeypatch):
        from app.services.scheduler import check_storage_permissions_job_func

        cold = tmp_path / "restored_perm_cold"
        cold.mkdir()
        loc = ColdStorageLocation(name="Restored Perm", path=str(cold))
        loc.permissions_error = "old error"
        db_session.add(loc)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.scheduler._check_path_permissions", MagicMock(return_value=[])
        )
        check_storage_permissions_job_func()
        db_session.refresh(loc)
        assert loc.permissions_error is None
