import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import (
    ColdStorageBackendType,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    PinnedFile,
    StorageType,
)
from app.services.file_freezer import FileFreezer


@pytest.mark.unit
class TestFileFreezer:
    def test_freeze_file_move_success(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        """Test freezing a file with MOVE operation."""
        # Setup hot storage
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content")

        # Setup cold storage
        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        # Create inventory
        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.operation_type = OperationType.MOVE
        monitored_path.source_path = str(hot_dir)
        db_session.add(monitored_path)
        db_session.commit()

        # Freeze
        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is True, f"Freezer failed: {error}"
        assert cold_path is not None
        assert Path(cold_path).exists()
        assert not hot_file.exists()

        # Verify DB
        db_session.refresh(inv)
        assert inv.storage_type == StorageType.COLD
        assert inv.file_path == cold_path
        assert inv.status == FileStatus.ACTIVE

        # Verify FileRecord
        record = (
            db_session.query(FileRecord).filter(FileRecord.path_id == monitored_path.id).first()
        )
        assert record is not None
        assert record.cold_storage_path == cold_path

    def test_freeze_file_copy_success(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        """Test freezing a file with COPY operation."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content")

        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.operation_type = OperationType.COPY
        monitored_path.source_path = str(hot_dir)
        db_session.add(monitored_path)
        # Set the location's operation_mode explicitly (no longer derived from path).
        storage_location.operation_mode = OperationType.COPY
        db_session.commit()

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is True, f"Freezer failed: {error}"
        assert hot_file.exists()  # Original stays for COPY
        assert Path(cold_path).exists()

    def test_freeze_file_symlink_success(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        """Test freezing a file with SYMLINK operation."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content")

        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.operation_type = OperationType.SYMLINK
        monitored_path.source_path = str(hot_dir)
        db_session.add(monitored_path)
        # Set the location's operation_mode explicitly (no longer derived from path).
        storage_location.operation_mode = OperationType.SYMLINK
        db_session.commit()

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is True, f"Freezer failed: {error}"
        assert hot_file.is_symlink()
        assert os.path.realpath(hot_file) == os.path.realpath(cold_path)

        # For SYMLINK, the real file is in cold storage, so the inventory must point there.
        # The hot path becomes a symlink (a pointer), not the authoritative location.
        db_session.refresh(inv)
        assert inv.file_path == cold_path

    def test_freeze_file_pin_success(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        """Test freezing and pinning a file."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content")

        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, pin=True, db=db_session
        )

        assert success is True, f"Freezer failed: {error}"
        # Check pinned file entry
        pin = db_session.query(PinnedFile).filter(PinnedFile.file_path == cold_path).first()
        assert pin is not None

    def test_freeze_file_already_in_cold(
        self, db_session, file_inventory_factory, storage_location
    ):
        """Test that freezing fails if file is already in cold storage."""
        inv = file_inventory_factory(path="/tmp/cold/file.txt", storage_type=StorageType.COLD)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is False
        assert "not in hot storage" in error.lower()

    def test_freeze_file_not_found_on_disk(
        self, db_session, file_inventory_factory, storage_location
    ):
        """Test that freezing fails if file is missing from hot storage."""
        inv = file_inventory_factory(path="/tmp/hot/missing.txt", storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is False
        assert "not found" in error.lower()

    def test_freeze_file_destination_exists(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        """Test that freezing fails if destination file already exists."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content")

        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        # Create file at destination
        dest_file = cold_dir / "test.txt"
        dest_file.write_text("existing")

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is False
        assert "already exists" in error.lower()

    def test_freeze_file_encrypted_success(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        """Test freezing a file with encryption enabled."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content to encrypt")

        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        storage_location.is_encrypted = True
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        monitored_path.operation_type = OperationType.MOVE

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is True, f"Freezer failed: {error}"
        assert cold_path.endswith(".ffenc")
        assert Path(cold_path).exists()
        assert not hot_file.exists()

        # Verify it's encrypted (not plain text)
        assert Path(cold_path).read_bytes() != b"content to encrypt"

        db_session.refresh(inv)
        assert inv.is_encrypted is True
        assert inv.file_path == cold_path

    def test_freeze_file_encrypted_remote_backend_success(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        """Test encrypted freeze flow for non-local backends (e.g., S3/GDrive)."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        hot_file = hot_dir / "remote-test.txt"
        hot_file.write_text("remote encryption content")

        storage_location.backend_type = ColdStorageBackendType.S3
        storage_location.path = "s3://bucket/archive"
        storage_location.is_encrypted = True
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        monitored_path.operation_type = OperationType.MOVE

        captured = {}

        class DummyBackend:
            def backend_name(self):
                return "s3"

            def capabilities(self):
                from app.services.cold_storage_backends.base import ColdStorageCapabilities

                return ColdStorageCapabilities(supports_move=True, supports_copy=True)

            def freeze_file(self, source_path, relative_path, location, operation_mode):
                captured["source_path"] = source_path
                captured["relative_path"] = relative_path
                captured["operation_mode"] = operation_mode
                captured["location"] = location
                assert source_path.exists()
                return True, None, f"s3://bucket/archive/{relative_path.as_posix()}", None

        monkeypatch.setattr(
            "app.services.file_freezer.get_backend", lambda _location: DummyBackend()
        )

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )

        assert success is True, f"Freezer failed: {error}"
        assert cold_path == f"s3://bucket/archive/ffenc_{inv.id}.ffenc"
        assert captured["relative_path"].as_posix().endswith(".ffenc")
        assert captured["operation_mode"] == OperationType.COPY
        assert not hot_file.exists()

        db_session.refresh(inv)
        assert inv.storage_type == StorageType.COLD
        assert inv.is_encrypted is True
        assert inv.file_path == cold_path

    def test_freeze_file_requires_database_session(self, file_inventory_factory, storage_location):
        inv = file_inventory_factory(path="/tmp/hot.txt", storage_type=StorageType.HOT)
        success, error, cold_path = FileFreezer.freeze_file(inv, None, storage_location, db=None)
        assert success is False
        assert "Database session required" in error
        assert cold_path is None

    def test_freeze_file_record_not_found(self, db_session, storage_location):
        from app.models import FileInventory

        ghost = FileInventory(
            path_id=1,
            file_path="/tmp/ghost.txt",
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.HOT,
        )
        ghost.id = 999999
        success, error, _cold = FileFreezer.freeze_file(
            ghost, None, storage_location, db=db_session
        )
        assert success is False
        assert "not found" in error.lower()

    def test_freeze_file_outside_monitored_base_uses_basename(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        hot_dir = tmp_path / "hot_base"
        hot_dir.mkdir()
        hot_file = tmp_path / "outside_hot" / "orphan.txt"
        hot_file.parent.mkdir()
        hot_file.write_text("orphan")

        cold_dir = tmp_path / "cold_orphan"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        db_session.commit()

        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        assert success is True, error
        assert cold_path.endswith("orphan.txt")

    def test_freeze_file_symlink_unsupported_backend(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        hot_dir = tmp_path / "hot_symlink_unsup"
        hot_dir.mkdir()
        hot_file = hot_dir / "link.txt"
        hot_file.write_text("data")

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        monitored_path.operation_type = OperationType.SYMLINK
        storage_location.operation_mode = OperationType.SYMLINK
        storage_location.backend_type = ColdStorageBackendType.S3
        storage_location.path = "s3://bucket"
        db_session.commit()

        class NoSymlinkBackend:
            def backend_name(self):
                return "s3"

            def capabilities(self):
                from app.services.cold_storage_backends.base import ColdStorageCapabilities

                return ColdStorageCapabilities(supports_symlink=False)

        monkeypatch.setattr(
            "app.services.file_freezer.get_backend", lambda _loc: NoSymlinkBackend()
        )
        success, error, _cold = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        assert success is False
        assert "not supported" in error.lower()

    def test_freeze_file_encrypted_local_encrypt_failure(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        hot_dir = tmp_path / "hot_enc_fail"
        hot_dir.mkdir()
        hot_file = hot_dir / "secret.txt"
        hot_file.write_text("secret")
        cold_dir = tmp_path / "cold_enc_fail"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        storage_location.is_encrypted = True
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        db_session.commit()

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.encrypt_file",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("encrypt failed")),
        )
        success, error, _cold = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        assert success is False
        assert "encrypt" in error.lower()
        db_session.refresh(inv)
        assert inv.status == FileStatus.ACTIVE

    def test_freeze_file_backend_failure_rolls_back_status(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        hot_dir = tmp_path / "hot_backend_fail"
        hot_dir.mkdir()
        hot_file = hot_dir / "fail.txt"
        hot_file.write_text("data")
        cold_dir = tmp_path / "cold_backend_fail"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        db_session.commit()

        class FailingBackend:
            def backend_name(self):
                return "local"

            def capabilities(self):
                from app.services.cold_storage_backends.base import ColdStorageCapabilities

                return ColdStorageCapabilities()

            def freeze_file(self, **_kwargs):
                return False, "backend move failed", None, None

        monkeypatch.setattr("app.services.file_freezer.get_backend", lambda _loc: FailingBackend())
        success, error, _cold = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        assert success is False
        assert "backend move failed" in error
        db_session.refresh(inv)
        assert inv.status == FileStatus.ACTIVE

    def test_freeze_file_encrypted_remote_backend_failure(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        hot_dir = tmp_path / "hot_remote_enc_fail"
        hot_dir.mkdir()
        hot_file = hot_dir / "remote-fail.txt"
        hot_file.write_text("remote")
        storage_location.backend_type = ColdStorageBackendType.S3
        storage_location.path = "s3://bucket/fail"
        storage_location.is_encrypted = True
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        db_session.commit()

        class RemoteFailBackend:
            def backend_name(self):
                return "s3"

            def capabilities(self):
                from app.services.cold_storage_backends.base import ColdStorageCapabilities

                return ColdStorageCapabilities()

            def freeze_file(self, **_kwargs):
                return False, "remote upload failed", None, None

        monkeypatch.setattr(
            "app.services.file_freezer.get_backend", lambda _loc: RemoteFailBackend()
        )
        success, error, _cold = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        assert success is False
        assert "remote upload failed" in error

    def test_freeze_file_pin_copy_pins_hot_path(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        hot_dir = tmp_path / "hot_pin_copy"
        hot_dir.mkdir()
        hot_file = hot_dir / "pin_copy.txt"
        hot_file.write_text("copy-pin")
        cold_dir = tmp_path / "cold_pin_copy"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        storage_location.operation_mode = OperationType.COPY
        db_session.add(storage_location)
        db_session.commit()

        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        monitored_path.source_path = str(hot_dir)
        monitored_path.operation_type = OperationType.COPY
        db_session.commit()

        success, _error, _cold = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, pin=True, db=db_session
        )
        assert success is True
        pin = db_session.query(PinnedFile).filter(PinnedFile.file_path == str(hot_file)).first()
        assert pin is not None
