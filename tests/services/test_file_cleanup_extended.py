"""Extended file cleanup service tests."""

from unittest.mock import MagicMock

import pytest

from app.models import FileInventory, FileRecord, FileStatus, OperationType, StorageType
from app.services.file_cleanup import FileCleanup


@pytest.mark.unit
class TestFileCleanupExtended:
    def test_cleanup_missing_files_move_operation(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir()
        cold_dir = tmp_path / "cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.commit()

        inv = file_inventory_factory(path=str(cold_dir / "gone.bin"), storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_dir / "gone.bin"),
            cold_storage_path=str(cold_dir / "gone.bin"),
            file_size=10,
            operation_type=OperationType.MOVE,
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(record)
        db_session.commit()

        results = FileCleanup.cleanup_missing_files(db_session, path_id=inv.path_id)
        assert results["removed"] >= 1
        assert db_session.query(FileRecord).count() == 0
        refreshed = db_session.get(FileInventory, inv.id)
        assert refreshed.status == FileStatus.MISSING

    def test_cleanup_copy_both_missing(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        cold_dir = tmp_path / "cold_copy"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.commit()

        inv = file_inventory_factory(path=str(cold_dir / "copy.bin"), storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(tmp_path / "hot_copy" / "copy.bin"),
            cold_storage_path=str(cold_dir / "copy.bin"),
            file_size=5,
            operation_type=OperationType.COPY,
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(record)
        db_session.commit()

        results = FileCleanup.cleanup_missing_files(db_session)
        assert results["removed"] == 1

    def test_cleanup_symlink_broken_target(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        hot_dir = tmp_path / "hot_sym"
        hot_dir.mkdir()
        cold_dir = tmp_path / "cold_sym"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.commit()

        hot_link = hot_dir / "link.txt"
        hot_link.symlink_to(cold_dir / "missing-target.txt")

        inv = file_inventory_factory(
            path=str(cold_dir / "missing-target.txt"), storage_type=StorageType.COLD
        )
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_link),
            cold_storage_path=str(cold_dir / "missing-target.txt"),
            file_size=10,
            operation_type=OperationType.SYMLINK,
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(record)
        db_session.commit()

        results = FileCleanup.cleanup_missing_files(db_session)
        assert results["removed"] == 1

    def test_cleanup_missing_inventory_entries_only(self, db_session, file_inventory_factory):
        inv = file_inventory_factory(path="/tmp/missing-only.txt", storage_type=StorageType.HOT)
        inv.status = FileStatus.MISSING
        db_session.commit()

        results = FileCleanup.cleanup_missing_inventory_files(db_session)
        assert results["removed"] == 1
        assert db_session.get(FileInventory, inv.id) is None

    def test_cleanup_remote_backend_exists_check(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        inv = file_inventory_factory(path="s3://bucket/key", storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(tmp_path / "orig.bin"),
            cold_storage_path="s3://bucket/key",
            file_size=1,
            operation_type=OperationType.MOVE,
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.return_value = False
        monkeypatch.setattr("app.services.file_cleanup.get_backend", lambda _loc: mock_backend)

        results = FileCleanup.cleanup_missing_files(db_session)
        assert results["removed"] == 1

    def test_cleanup_skips_when_backend_check_raises(
        self, db_session, tmp_path, file_inventory_factory, storage_location, monkeypatch
    ):
        inv = file_inventory_factory(path="s3://bucket/key2", storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(tmp_path / "orig2.bin"),
            cold_storage_path="s3://bucket/key2",
            file_size=1,
            operation_type=OperationType.MOVE,
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(record)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.backend_name.return_value = "s3"
        mock_backend.exists.side_effect = RuntimeError("network")
        monkeypatch.setattr("app.services.file_cleanup.get_backend", lambda _loc: mock_backend)

        results = FileCleanup.cleanup_missing_files(db_session)
        assert results["removed"] == 0

    def test_cleanup_move_parent_cache_short_circuit(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        cold_dir = tmp_path / "cold_parent"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.commit()

        inv = file_inventory_factory(
            path=str(cold_dir / "nested/a.bin"), storage_type=StorageType.COLD
        )
        for suffix in ("a.bin", "b.bin"):
            db_session.add(
                FileRecord(
                    path_id=inv.path_id,
                    original_path=str(tmp_path / "hot" / suffix),
                    cold_storage_path=str(cold_dir / "nested" / suffix),
                    file_size=1,
                    operation_type=OperationType.MOVE,
                    cold_storage_location_id=storage_location.id,
                )
            )
        db_session.commit()

        results = FileCleanup.cleanup_missing_files(db_session)
        assert results["checked"] == 2
        assert results["removed"] == 2

    def test_cleanup_uses_parent_existence_cache(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        cold_dir = tmp_path / "missing_parent_dir"
        inv1 = file_inventory_factory(path=str(cold_dir / "a.bin"), storage_type=StorageType.COLD)
        inv2 = file_inventory_factory(
            path=str(cold_dir / "b.bin"),
            storage_type=StorageType.COLD,
            path_name="Cache Path 2",
        )
        for inv, name in ((inv1, "a.bin"), (inv2, "b.bin")):
            db_session.add(
                FileRecord(
                    path_id=inv.path_id,
                    original_path=str(tmp_path / "hot" / name),
                    cold_storage_path=str(cold_dir / name),
                    file_size=1,
                    operation_type=OperationType.MOVE,
                    cold_storage_location_id=storage_location.id,
                )
            )
        db_session.commit()
        results = FileCleanup.cleanup_missing_files(db_session)
        assert results["removed"] == 2
