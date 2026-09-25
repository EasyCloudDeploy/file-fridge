"""Extended FileThawer unit tests."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import FileInventory, FileRecord, OperationType, StorageType
from app.services.file_thawer import FileThawer


@pytest.mark.unit
class TestFileThawerExtended:
    def test_thaw_requires_database_session(self, tmp_path):
        record = FileRecord(
            path_id=1,
            original_path=str(tmp_path / "hot.txt"),
            cold_storage_path=str(tmp_path / "cold.txt"),
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        ok, err = FileThawer.thaw_file(record, db=None)
        assert ok is False
        assert "Database session required" in err

    def test_temp_destination_helpers(self, tmp_path):
        final = tmp_path / "file.bin"
        temp = FileThawer._temp_destination_for(final)
        assert temp.name == "file.bin.tmp"
        temp.write_text("staging")
        FileThawer._cleanup_temp_destination(temp, final)
        assert not temp.exists()

    def test_finalize_staged_move(self, tmp_path):
        source = tmp_path / "source.bin"
        source.write_bytes(b"abc")
        staged = tmp_path / "staged.bin"
        staged.write_bytes(b"abc")
        final = tmp_path / "final.bin"
        stat_info = source.stat()

        FileThawer._finalize_staged_move(source, staged, final, stat_info)
        assert final.exists()
        assert not source.exists()
        assert not staged.exists()

    def test_thaw_missing_cold_file_returns_error(
        self, db_session, tmp_path, file_inventory_factory
    ):
        hot_file = tmp_path / "hot_missing.txt"
        cold_file = tmp_path / "cold_missing.txt"
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "not found" in (err or "").lower()

    def test_thaw_local_move_success_and_pin(self, db_session, monitored_path_factory, tmp_path):
        hot_dir = tmp_path / "thaw_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "thaw_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Thaw Local", str(hot_dir))
        cold_file = cold_dir / "thaw_me.txt"
        cold_file.write_text("thawed")
        hot_file = hot_dir / "thaw_me.txt"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=cold_file.stat().st_size,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=inv.file_size,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        ok, err = FileThawer.thaw_file(record, pin=True, db=db_session, initiated_by="test")
        assert ok is True
        assert err is None
        assert hot_file.exists()
        assert not cold_file.exists()
        from app.models import PinnedFile

        assert db_session.query(PinnedFile).filter(PinnedFile.file_path == str(hot_file)).count()

    def test_thaw_remote_backend_success(
        self, db_session, tmp_path, monitored_path_factory, monkeypatch
    ):
        from unittest.mock import MagicMock

        from app.models import ColdStorageBackendType, ColdStorageLocation

        hot_dir = tmp_path / "remote_thaw_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Remote Thaw Path", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Remote Thaw",
            path="s3://bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot = hot_dir / "remote_hot.bin"
        cold_ref = "s3://bucket/remote_hot.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=cold_ref,
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            cold_storage_location_id=cold_loc.id,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=4,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = True
        mock_backend.thaw_file.return_value = (True, None)
        monkeypatch.setattr("app.services.file_thawer.get_backend", lambda _loc: mock_backend)
        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            lambda _p: "checksum",
        )

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert err is None

    def test_finalize_staged_move_metadata_failure(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        source = tmp_path / "meta_src.bin"
        source.write_bytes(b"abc")
        staged = tmp_path / "meta_staged.bin"
        staged.write_bytes(b"abc")
        final = tmp_path / "meta_final.bin"
        stat_info = source.stat()
        monkeypatch.setattr(
            "app.services.file_thawer.shutil.copystat",
            MagicMock(side_effect=OSError("copystat failed")),
        )
        with pytest.raises(OSError):
            FileThawer._finalize_staged_move(source, staged, final, stat_info)

    def test_thaw_symlink_operation(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "symlink_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "symlink_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Symlink Thaw", str(hot_dir))
        cold_file = cold_dir / "link_target.txt"
        cold_file.write_text("linked")
        hot_link = hot_dir / "link_target.txt"
        hot_link.symlink_to(cold_file)
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=7,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_link),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.SYMLINK,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot_link.exists()
        assert not hot_link.is_symlink()
        assert hot_link.read_text() == "linked"

    def test_thaw_copy_operation_original_exists(
        self, db_session, monitored_path_factory, tmp_path
    ):
        hot_dir = tmp_path / "copy_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "copy_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Copy Thaw", str(hot_dir))
        cold_file = cold_dir / "copy_me.txt"
        cold_file.write_text("cold-copy")
        hot_file = hot_dir / "copy_me.txt"
        hot_file.write_text("cold-copy")
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=9,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=9,
            operation_type=OperationType.COPY,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot_file.read_text() == "cold-copy"
        assert not cold_file.exists()

    def test_thaw_encrypted_local_file(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):

        hot_dir = tmp_path / "enc_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "enc_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Enc Thaw", str(hot_dir))
        cold_file = cold_dir / "secret.bin.ffenc"
        cold_file.write_bytes(b"cipher")
        hot_file = hot_dir / "secret.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=6,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            is_encrypted=True,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=6,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file",
            lambda _db, src, dest: dest.write_bytes(b"plain"),
        )
        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            lambda _p: "checksum",
        )

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot_file.read_bytes() == b"plain"

    def test_thaw_checksum_mismatch(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "checksum_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "checksum_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Checksum Thaw", str(hot_dir))
        cold_file = cold_dir / "mismatch.bin"
        cold_file.write_bytes(b"abc")
        hot_file = hot_dir / "mismatch.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=3,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=3,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            MagicMock(side_effect=["before", "after"]),
        )
        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "Checksum verification failed" in (err or "")

    def test_move_preserving_timestamps_cross_filesystem(self, tmp_path):
        from pathlib import Path

        source = tmp_path / "cross_src.bin"
        source.write_bytes(b"x" * 2048)
        dest = tmp_path / "cross_dest.bin"
        progress_calls = []

        with patch.object(Path, "rename", side_effect=OSError("cross device")):
            prepared, stat_info = FileThawer._move_preserving_timestamps(
                source, dest, progress_callback=lambda n: progress_calls.append(n)
            )
        assert prepared.exists()
        assert stat_info.st_size == 2048
        assert progress_calls

    def test_cleanup_temp_noop_when_same_path(self, tmp_path):
        final = tmp_path / "same.bin"
        FileThawer._cleanup_temp_destination(final, final)

    def test_thaw_local_missing_with_drive_hints(
        self, db_session, monitored_path_factory, tmp_path
    ):
        from app.models import ColdStorageBackendType, ColdStorageLocation

        hot_dir = tmp_path / "hint_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Hint Thaw", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Offline Cold",
            path=str(tmp_path / "hint_cold"),
            backend_type=ColdStorageBackendType.LOCAL,
            local_drive_label="MyDrive",
            local_drive_identifier="disk-uuid",
            local_drive_mount_path="/Volumes/MyDrive",
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot_file = hot_dir / "missing.bin"
        cold_ref = str(tmp_path / "hint_cold" / "missing.bin")
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "label=MyDrive" in (err or "")
        assert "mount=/Volumes/MyDrive" in (err or "")

    def test_thaw_copy_original_missing_moves_from_cold(
        self, db_session, monitored_path_factory, tmp_path
    ):
        hot_dir = tmp_path / "copy_miss_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "copy_miss_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Copy Missing Original", str(hot_dir))
        cold_file = cold_dir / "restore_me.txt"
        cold_file.write_text("restored")
        hot_file = hot_dir / "restore_me.txt"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=9,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=9,
            operation_type=OperationType.COPY,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot_file.read_text() == "restored"
        assert not cold_file.exists()

    def test_thaw_encrypted_copy_original_exists_unlinks_cold_only(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "enc_copy_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "enc_copy_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Enc Copy Thaw", str(hot_dir))
        cold_file = cold_dir / "keep_hot.bin.ffenc"
        cold_file.write_bytes(b"cipher")
        hot_file = hot_dir / "keep_hot.bin"
        hot_file.write_bytes(b"plain-hot")
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=6,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            is_encrypted=True,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=6,
            operation_type=OperationType.COPY,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        decrypt_mock = MagicMock(side_effect=AssertionError("should not decrypt when hot exists"))
        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file", decrypt_mock
        )

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot_file.read_bytes() == b"plain-hot"
        assert not cold_file.exists()

    def test_thaw_finalize_failure_returns_error(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "finalize_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "finalize_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Finalize Fail", str(hot_dir))
        cold_file = cold_dir / "finalize.bin"
        cold_file.write_bytes(b"abc")
        hot_file = hot_dir / "finalize.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=3,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=3,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        staged = hot_file.with_name("finalize.bin.tmp")
        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._move_preserving_timestamps",
            lambda *_a, **_k: (staged, cold_file.stat()),
        )
        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._finalize_staged_move",
            MagicMock(side_effect=OSError("finalize failed")),
        )

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "finalize" in (err or "").lower()

    def test_thaw_remote_encrypted_move(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType, ColdStorageLocation

        hot_dir = tmp_path / "remote_enc_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Remote Enc Thaw", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Remote Enc",
            path="s3://enc-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot = hot_dir / "remote_enc.bin"
        cold_ref = "s3://enc-bucket/remote_enc.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=cold_ref,
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            cold_storage_location_id=cold_loc.id,
            is_encrypted=True,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=4,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = True
        mock_backend.thaw_file.return_value = (True, None)
        monkeypatch.setattr("app.services.file_thawer.get_backend", lambda _loc: mock_backend)

        def _decrypt(_db, src, dest):
            dest.write_bytes(b"decrypted")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file", _decrypt
        )
        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            lambda _p: "checksum",
        )

        ok, err = FileThawer.thaw_file(record, pin=True, db=db_session)
        assert ok is True
        assert hot.read_bytes() == b"decrypted"

    def test_thaw_remote_encrypted_copy_original_exists(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType, ColdStorageLocation

        hot_dir = tmp_path / "remote_enc_copy_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Remote Enc Copy", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Remote Enc Copy Loc",
            path="s3://enc-copy",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot = hot_dir / "remote_enc_copy.bin"
        hot.write_bytes(b"still-here")
        cold_ref = "s3://enc-copy/remote_enc_copy.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=cold_ref,
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            cold_storage_location_id=cold_loc.id,
            is_encrypted=True,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=4,
            operation_type=OperationType.COPY,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = True
        mock_backend.thaw_file.return_value = (True, None)
        monkeypatch.setattr("app.services.file_thawer.get_backend", lambda _loc: mock_backend)
        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            lambda _p: "checksum",
        )

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot.read_bytes() == b"still-here"
        mock_backend.thaw_file.assert_called_once()

    def test_thaw_remote_not_found(self, db_session, monitored_path_factory, tmp_path, monkeypatch):
        from app.models import ColdStorageBackendType, ColdStorageLocation

        hot_dir = tmp_path / "remote_missing_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Remote Missing", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Remote Missing Loc",
            path="s3://missing",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot = hot_dir / "gone.bin"
        cold_ref = "s3://missing/gone.bin"
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = False
        monkeypatch.setattr("app.services.file_thawer.get_backend", lambda _loc: mock_backend)

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "not found" in (err or "").lower()

    def test_thaw_remote_backend_error(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType, ColdStorageLocation

        hot_dir = tmp_path / "remote_err_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Remote Err", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Remote Err Loc",
            path="s3://err",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot = hot_dir / "err.bin"
        cold_ref = "s3://err/err.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=cold_ref,
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            cold_storage_location_id=cold_loc.id,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = True
        mock_backend.thaw_file.return_value = (False, "backend thaw failed")
        monkeypatch.setattr("app.services.file_thawer.get_backend", lambda _loc: mock_backend)

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "backend thaw failed" in (err or "")

    def test_thaw_remote_pin_skips_existing_pin(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        from app.models import ColdStorageBackendType, ColdStorageLocation, PinnedFile

        hot_dir = tmp_path / "remote_pin_hot"
        hot_dir.mkdir()
        path = monitored_path_factory("Remote Pin", str(hot_dir))
        cold_loc = ColdStorageLocation(
            name="Remote Pin Loc",
            path="s3://pin",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(cold_loc)
        db_session.flush()
        hot = hot_dir / "pin_me.bin"
        hot.write_bytes(b"x")
        cold_ref = "s3://pin/pin_me.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=cold_ref,
            file_size=1,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            cold_storage_location_id=cold_loc.id,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot),
            cold_storage_path=cold_ref,
            cold_storage_location_id=cold_loc.id,
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.add(PinnedFile(path_id=path.id, file_path=str(hot)))
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = True
        mock_backend.thaw_file.return_value = (True, None)
        monkeypatch.setattr("app.services.file_thawer.get_backend", lambda _loc: mock_backend)
        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            lambda _p: "checksum",
        )

        ok, err = FileThawer.thaw_file(record, pin=True, db=db_session)
        assert ok is True
        assert db_session.query(PinnedFile).filter(PinnedFile.file_path == str(hot)).count() == 1

    def test_thaw_decrypt_failure_returns_error(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "dec_fail_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "dec_fail_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Decrypt Fail", str(hot_dir))
        cold_file = cold_dir / "bad.ffenc"
        cold_file.write_bytes(b"cipher")
        hot_file = hot_dir / "bad.bin"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=6,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            is_encrypted=True,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=6,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("decrypt boom")),
        )
        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "decrypt" in (err or "").lower()

    def test_thaw_copy_remove_cold_failure(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "copy_rm_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "copy_rm_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Copy Remove Fail", str(hot_dir))
        cold_file = cold_dir / "stay.txt"
        cold_file.write_text("cold")
        hot_file = hot_dir / "stay.txt"
        hot_file.write_text("cold")
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=4,
            operation_type=OperationType.COPY,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        original_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if self.resolve() == cold_file.resolve():
                raise OSError("permission denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_unlink)
        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "cold storage" in (err or "").lower()

    def test_thaw_move_failure_returns_error(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "move_fail_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "move_fail_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Move Fail", str(hot_dir))
        cold_file = cold_dir / "move.txt"
        cold_file.write_text("data")
        hot_file = hot_dir / "move.txt"
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=4,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=4,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._move_preserving_timestamps",
            MagicMock(side_effect=OSError("move failed")),
        )
        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is False
        assert "move" in (err or "").lower()

    def test_thaw_encrypted_removes_symlink_before_decrypt(
        self, db_session, monitored_path_factory, tmp_path, monkeypatch
    ):
        hot_dir = tmp_path / "enc_symlink_hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "enc_symlink_cold"
        cold_dir.mkdir()
        path = monitored_path_factory("Enc Symlink", str(hot_dir))
        cold_file = cold_dir / "linked.ffenc"
        cold_file.write_bytes(b"cipher")
        hot_link = hot_dir / "linked.bin"
        hot_link.symlink_to("/tmp/nowhere")
        inv = FileInventory(
            path_id=path.id,
            file_path=str(cold_file),
            file_size=6,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            is_encrypted=True,
        )
        record = FileRecord(
            path_id=path.id,
            original_path=str(hot_link),
            cold_storage_path=str(cold_file),
            file_size=6,
            operation_type=OperationType.MOVE,
        )
        db_session.add_all([inv, record])
        db_session.commit()

        def _decrypt(_db, _src, dest):
            dest.write_bytes(b"plain")

        monkeypatch.setattr(
            "app.services.encryption_service.file_encryption_service.decrypt_file", _decrypt
        )
        monkeypatch.setattr(
            "app.services.file_thawer.checksum_verifier.calculate_checksum",
            lambda _p: "checksum",
        )

        ok, err = FileThawer.thaw_file(record, db=db_session)
        assert ok is True
        assert hot_link.read_bytes() == b"plain"
        assert not hot_link.is_symlink()
