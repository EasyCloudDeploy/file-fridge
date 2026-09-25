from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import ColdStorageBackendType, ColdStorageLocation, FileRecord, OperationType
from app.services.path_reverser import PathReverser


@pytest.mark.unit
class TestPathReverser:
    def test_reverse_move(self, db_session, tmp_path):
        """Test reversing a MOVE operation."""
        hot_file = tmp_path / "hot" / "file.txt"
        cold_file = tmp_path / "cold" / "file.txt"

        cold_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.write_text("content")

        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 1
        assert not cold_file.exists()
        assert hot_file.exists()
        assert hot_file.read_text() == "content"
        assert db_session.query(FileRecord).count() == 0

    def test_reverse_copy_original_missing(self, db_session, tmp_path):
        """Test reversing a COPY operation when original is missing (moves it back)."""
        hot_file = tmp_path / "hot" / "file.txt"
        cold_file = tmp_path / "cold" / "file.txt"

        cold_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.write_text("content")

        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.COPY,
        )
        db_session.add(record)
        db_session.commit()

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 1
        assert not cold_file.exists()
        assert hot_file.exists()
        assert hot_file.read_text() == "content"

    def test_reverse_copy_original_exists(self, db_session, tmp_path):
        """Test reversing a COPY operation when original exists (unlinks cold copy)."""
        hot_file = tmp_path / "hot" / "file.txt"
        cold_file = tmp_path / "cold" / "file.txt"

        hot_file.parent.mkdir(parents=True, exist_ok=True)
        hot_file.write_text("content")
        cold_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.write_text("content")

        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.COPY,
        )
        db_session.add(record)
        db_session.commit()

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 1
        assert not cold_file.exists()
        assert hot_file.exists()

    def test_reverse_symlink_move_failure(self, db_session, tmp_path, monkeypatch):
        hot_file = tmp_path / "hot_sym_fail" / "file.txt"
        cold_file = tmp_path / "cold_sym_fail" / "file.txt"
        hot_file.parent.mkdir(parents=True)
        cold_file.parent.mkdir(parents=True)
        cold_file.write_text("content")
        hot_file.symlink_to(cold_file)
        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.SYMLINK,
        )
        db_session.add(record)
        db_session.commit()

        from app.services.file_mover import FileMover

        monkeypatch.setattr(
            FileMover,
            "move_with_rollback",
            lambda **kwargs: (False, "move failed", None),
        )
        results = PathReverser.reverse_path_operations(1, db_session)
        assert results["files_reversed"] == 0
        assert any("move failed" in err.lower() for err in results["errors"])

    def test_reverse_symlink(self, db_session, tmp_path):
        """Test reversing a SYMLINK operation."""
        hot_file = tmp_path / "hot" / "file.txt"
        cold_file = tmp_path / "cold" / "file.txt"

        hot_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.write_text("content")
        # In some environments, symlink might need special care, but standard Path works
        hot_file.symlink_to(cold_file)

        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.SYMLINK,
        )
        db_session.add(record)
        db_session.commit()

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 1
        assert not cold_file.exists()
        assert hot_file.exists()
        assert not hot_file.is_symlink()

    def test_reverse_file_not_found(self, db_session, tmp_path):
        """Test error handling when file is not found in cold storage."""
        record = FileRecord(
            path_id=1,
            original_path="/tmp/missing",
            cold_storage_path="/tmp/cold_missing",
            file_size=0,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 0
        assert len(results["errors"]) == 1
        assert "not found" in results["errors"][0].lower()

    def test_reverse_unknown_operation(self, db_session, tmp_path):
        """Test error handling for unknown operation type."""
        cold_file = tmp_path / "cold_unknown"
        cold_file.write_text("content")

        record = FileRecord(
            path_id=1,
            original_path="/tmp/unknown",
            cold_storage_path=str(cold_file),
            file_size=0,
            operation_type="UNKNOWN",  # Force an unknown type
        )
        db_session.add(record)
        db_session.commit()

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 0
        assert len(results["errors"]) == 1
        assert "not among the defined enum values" in results["errors"][0].lower()

    def test_reverse_move_mkdir_failure(self, db_session, tmp_path, monkeypatch):
        hot_file = tmp_path / "hot_mkdir_fail" / "file.txt"
        cold_file = tmp_path / "cold_mkdir_fail" / "file.txt"
        cold_file.parent.mkdir(parents=True)
        cold_file.write_text("content")
        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        original_mkdir = Path.mkdir

        def failing_mkdir(self, *args, **kwargs):
            if "hot_mkdir_fail" in str(self):
                raise OSError("mkdir failed")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)
        results = PathReverser.reverse_path_operations(1, db_session)
        assert results["files_reversed"] == 0
        assert any("mkdir failed" in err for err in results["errors"])

    def test_reverse_move_failure(self, db_session, tmp_path, monkeypatch):
        """Test handling of move failure during reversal."""
        hot_file = tmp_path / "hot_fail" / "file.txt"
        cold_file = tmp_path / "cold_fail" / "file.txt"

        cold_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.write_text("content")

        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        def mock_move_with_rollback(**kwargs):
            return False, "Disk full", None

        from app.services.file_mover import FileMover

        monkeypatch.setattr(FileMover, "move_with_rollback", mock_move_with_rollback)

        results = PathReverser.reverse_path_operations(1, db_session)

        assert results["files_reversed"] == 0
        assert len(results["errors"]) == 1
        assert "disk full" in results["errors"][0].lower()

    def test_reverse_remote_backend_delegates_to_thawer(self, db_session, tmp_path, monkeypatch):
        cold_file = tmp_path / "cold_remote"
        cold_file.write_text("remote")
        loc = ColdStorageLocation(
            name="S3 Rev",
            path="s3://rev-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(loc)
        db_session.flush()
        record = FileRecord(
            path_id=1,
            original_path=str(tmp_path / "hot_remote.txt"),
            cold_storage_path=str(cold_file),
            cold_storage_location_id=loc.id,
            file_size=7,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        thaw_mock = MagicMock(return_value=(True, None))
        with patch("app.services.path_reverser.FileThawer.thaw_file", thaw_mock):
            results = PathReverser.reverse_path_operations(1, db_session)
        assert results["files_reversed"] == 1
        thaw_mock.assert_called_once()

    def test_reverse_per_file_exception(self, db_session, tmp_path, monkeypatch):
        cold_file = tmp_path / "cold_exc"
        cold_file.write_text("x")
        record = FileRecord(
            path_id=1,
            original_path=str(tmp_path / "hot_exc.txt"),
            cold_storage_path=str(cold_file),
            file_size=1,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()

        monkeypatch.setattr(
            PathReverser,
            "_reverse_file_operation",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        results = PathReverser.reverse_path_operations(1, db_session)
        assert results["files_reversed"] == 0
        assert any("boom" in err for err in results["errors"])

    def test_reverse_path_operations_top_level_error(self, db_session, monkeypatch):
        monkeypatch.setattr(
            db_session,
            "query",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        results = PathReverser.reverse_path_operations(1, db_session)
        assert results["files_reversed"] == 0
        assert any("db down" in err for err in results["errors"])

    def test_reverse_copy_unlink_failure(self, db_session, tmp_path, monkeypatch):
        hot_file = tmp_path / "hot_unlink" / "file.txt"
        cold_file = tmp_path / "cold_unlink" / "file.txt"
        hot_file.parent.mkdir(parents=True)
        hot_file.write_text("content")
        cold_file.parent.mkdir(parents=True)
        cold_file.write_text("content")

        record = FileRecord(
            path_id=1,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.COPY,
        )
        db_session.add(record)
        db_session.commit()

        cold_str = str(cold_file)
        original_unlink = Path.unlink

        def patched_unlink(self, *args, **kwargs):
            if str(self) == cold_str:
                raise OSError("unlink denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", patched_unlink)
        results = PathReverser.reverse_path_operations(1, db_session)
        assert results["files_reversed"] == 0
        assert any("unlink" in err.lower() for err in results["errors"])
