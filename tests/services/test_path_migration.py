import os
import shutil
from pathlib import Path

import pytest

from app.models import FileInventory, FileRecord, StorageType, OperationType
from app.services.path_migration import PathMigrationService


@pytest.mark.unit
class TestPathMigrationService:
    def test_check_existing_files(self, db_session, tmp_path, file_inventory_factory):
        """Test checking for existing files in a cold storage location."""
        old_path = tmp_path / "old_cold"
        old_path.mkdir()
        
        # Create some files in filesystem
        f1 = old_path / "file1.txt"
        f1.write_text("content1")
        f2 = old_path / "subdir" / "file2.txt"
        f2.parent.mkdir()
        f2.write_text("content2")
        
        # Create some database records
        # file_inventory_factory creates MonitoredPath too.
        # We need to ensure the path_id matches.
        inv1 = file_inventory_factory(path=str(f1), storage_type=StorageType.COLD)
        path_id = inv1.path_id
        
        # Add a FileRecord
        record1 = FileRecord(
            path_id=path_id,
            original_path="/tmp/hot/file1.txt",
            cold_storage_path=str(f1),
            file_size=8,
            operation_type=OperationType.MOVE
        )
        db_session.add(record1)
        db_session.commit()
        
        result = PathMigrationService.check_existing_files(str(old_path), path_id, db_session)
        
        assert result["has_files"] is True
        assert result["file_records_count"] == 1
        assert result["inventory_count"] == 1
        assert result["filesystem_count"] == 2
        assert len(result["file_records"]) == 1
        assert len(result["file_inventory"]) == 1
        assert len(result["filesystem_files"]) == 2

    def test_migrate_files_success(self, db_session, tmp_path, file_inventory_factory):
        """Test successful migration of files between cold storage locations."""
        old_path = tmp_path / "old_cold"
        new_path = tmp_path / "new_cold"
        old_path.mkdir()
        
        f1 = old_path / "file1.txt"
        f1.write_text("content1")
        
        inv1 = file_inventory_factory(path=str(f1), storage_type=StorageType.COLD)
        path_id = inv1.path_id
        
        record1 = FileRecord(
            path_id=path_id,
            original_path="/tmp/hot/file1.txt",
            cold_storage_path=str(f1),
            file_size=8,
            operation_type=OperationType.MOVE
        )
        db_session.add(record1)
        db_session.commit()
        
        success, error, stats = PathMigrationService.migrate_files(
            str(old_path), str(new_path), path_id, db_session
        )
        
        assert success is True
        assert error is None
        assert stats["files_moved"] == 1
        assert stats["records_updated"] == 2  # 1 inventory + 1 record
        
        assert not f1.exists()
        assert (new_path / "file1.txt").exists()
        assert (new_path / "file1.txt").read_text() == "content1"
        
        # Verify DB updated
        db_session.refresh(inv1)
        assert inv1.file_path == str(new_path / "file1.txt")
        db_session.refresh(record1)
        assert record1.cold_storage_path == str(new_path / "file1.txt")
        
        # Old directory should be removed if empty
        assert not old_path.exists()

    def test_migrate_no_files(self, db_session, tmp_path):
        """Test migration when no files exist."""
        old_path = tmp_path / "old_cold"
        new_path = tmp_path / "new_cold"
        
        success, error, stats = PathMigrationService.migrate_files(
            str(old_path), str(new_path), 1, db_session
        )
        
        assert success is True
        assert stats["files_moved"] == 0
        assert new_path.exists()

    def test_migrate_with_failure(self, db_session, tmp_path, file_inventory_factory, monkeypatch):
        """Test migration with a file move failure."""
        old_path = tmp_path / "old_cold"
        new_path = tmp_path / "new_cold"
        old_path.mkdir()
        
        f1 = old_path / "file1.txt"
        f1.write_text("content1")
        
        inv1 = file_inventory_factory(path=str(f1), storage_type=StorageType.COLD)
        path_id = inv1.path_id
        
        def mock_move(src, dst):
            raise OSError("Permission denied")
            
        import shutil
        monkeypatch.setattr(shutil, "move", mock_move)
        
        success, error, stats = PathMigrationService.migrate_files(
            str(old_path), str(new_path), path_id, db_session
        )
        
        assert success is False
        assert "failures" in error
        assert stats["files_failed"] == 1
        assert f1.exists()

    def test_abandon_files(self, db_session):
        """Test abandoning files in a cold storage location."""
        success, message = PathMigrationService.abandon_files("/some/path", 1, db_session)
        assert success is True
        assert "left in place" in message.lower()
