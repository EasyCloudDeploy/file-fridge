import os
import shutil
from pathlib import Path

import pytest

from app.models import FileRecord, OperationType
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
            operation_type=OperationType.MOVE
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
            operation_type=OperationType.COPY
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
            operation_type=OperationType.COPY
        )
        db_session.add(record)
        db_session.commit()
        
        results = PathReverser.reverse_path_operations(1, db_session)
        
        assert results["files_reversed"] == 1
        assert not cold_file.exists()
        assert hot_file.exists()

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
            operation_type=OperationType.SYMLINK
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
            operation_type=OperationType.MOVE
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
            operation_type="UNKNOWN"  # Force an unknown type
        )
        db_session.add(record)
        db_session.commit()
        
        results = PathReverser.reverse_path_operations(1, db_session)
        
        assert results["files_reversed"] == 0
        assert len(results["errors"]) == 1
        assert "not among the defined enum values" in results["errors"][0].lower()

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
            operation_type=OperationType.MOVE
        )
        db_session.add(record)
        db_session.commit()
        
        def mock_move(src, dst):
            raise OSError("Disk full")
            
        import shutil
        monkeypatch.setattr(shutil, "move", mock_move)
        
        results = PathReverser.reverse_path_operations(1, db_session)
        
        assert results["files_reversed"] == 0
        assert len(results["errors"]) == 1
        assert "disk full" in results["errors"][0].lower()
