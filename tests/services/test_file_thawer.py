import os
import shutil
from pathlib import Path

import pytest

from app.models import FileRecord, FileInventory, FileStatus, OperationType, StorageType, PinnedFile
from app.services.file_thawer import FileThawer


@pytest.mark.unit
class TestFileThawer:
    def test_thaw_file_move_success(self, db_session, tmp_path, file_inventory_factory):
        """Test thawing a MOVE operation."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir(parents=True, exist_ok=True)
        hot_file = hot_dir / "test.txt"
        
        cold_dir = tmp_path / "cold"
        cold_dir.mkdir(parents=True, exist_ok=True)
        cold_file = cold_dir / "test.txt"
        cold_file.write_text("content")
        
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD)
        
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.MOVE
        )
        db_session.add(record)
        db_session.commit()
        
        success, error = FileThawer.thaw_file(record, db=db_session)
        
        assert success is True, f"Thaw failed: {error}"
        assert not cold_file.exists()
        assert hot_file.exists()
        assert hot_file.read_text() == "content"
        
        db_session.refresh(inv)
        assert inv.storage_type == StorageType.HOT
        assert inv.file_path == str(hot_file)
        assert inv.status == FileStatus.ACTIVE
        assert db_session.query(FileRecord).count() == 0

    def test_thaw_file_encrypted_success(self, db_session, tmp_path, file_inventory_factory):
        """Test thawing an encrypted file."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir(parents=True, exist_ok=True)
        hot_file = hot_dir / "test.txt"
        
        cold_dir = tmp_path / "cold"
        cold_dir.mkdir(parents=True, exist_ok=True)
        cold_file = cold_dir / "test.txt.ffenc"
        
        # Actually encrypt it
        from app.services.encryption_service import file_encryption_service
        temp_src = tmp_path / "temp_src"
        temp_src.write_text("encrypted content")
        file_encryption_service.encrypt_file(db_session, temp_src, cold_file)
        
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD, is_encrypted=True)
        
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=17,
            operation_type=OperationType.MOVE
        )
        db_session.add(record)
        db_session.commit()
        
        success, error = FileThawer.thaw_file(record, db=db_session)
        
        assert success is True, f"Thaw failed: {error}"
        assert not cold_file.exists()
        assert hot_file.exists()
        assert hot_file.read_text() == "encrypted content"
        
        db_session.refresh(inv)
        assert inv.is_encrypted is False
        assert inv.storage_type == StorageType.HOT

    def test_thaw_file_copy_success(self, db_session, tmp_path, file_inventory_factory):
        """Test thawing a COPY operation."""
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir(parents=True, exist_ok=True)
        hot_file = hot_dir / "test.txt"
        hot_file.write_text("content") # Still exists for COPY
        
        cold_dir = tmp_path / "cold"
        cold_dir.mkdir(parents=True, exist_ok=True)
        cold_file = cold_dir / "test.txt"
        cold_file.write_text("content")
        
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.COPY
        )
        db_session.add(record)
        db_session.commit()
        
        success, error = FileThawer.thaw_file(record, db=db_session)
        
        assert success is True, f"Thaw failed: {error}"
        assert not cold_file.exists()
        assert hot_file.exists()

    def test_thaw_file_symlink_success(self, db_session, tmp_path, file_inventory_factory):
        """Test thawing a SYMLINK operation."""
        cold_dir = tmp_path / "cold"
        cold_dir.mkdir(parents=True, exist_ok=True)
        cold_file = cold_dir / "test.txt"
        cold_file.write_text("content")
        
        hot_dir = tmp_path / "hot"
        hot_dir.mkdir(parents=True, exist_ok=True)
        hot_file = hot_dir / "test.txt"
        hot_file.symlink_to(cold_file)
        
        inv = file_inventory_factory(path=str(hot_file), storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.SYMLINK
        )
        db_session.add(record)
        db_session.commit()
        
        success, error = FileThawer.thaw_file(record, db=db_session)
        
        assert success is True, f"Thaw failed: {error}"
        assert not cold_file.exists()
        assert hot_file.exists()
        assert not hot_file.is_symlink()

    def test_thaw_file_pin_success(self, db_session, tmp_path, file_inventory_factory):
        """Test thawing and pinning a file."""
        cold_file = tmp_path / "cold.txt"
        cold_file.write_text("content")
        hot_file = tmp_path / "hot.txt"
        
        inv = file_inventory_factory(path=str(cold_file), storage_type=StorageType.COLD)
        record = FileRecord(
            path_id=inv.path_id,
            original_path=str(hot_file),
            cold_storage_path=str(cold_file),
            file_size=7,
            operation_type=OperationType.MOVE
        )
        db_session.add(record)
        db_session.commit()
        
        success, error = FileThawer.thaw_file(record, pin=True, db=db_session)
        
        assert success is True, f"Thaw failed: {error}"
        pin = db_session.query(PinnedFile).filter(PinnedFile.file_path == str(hot_file)).first()
        assert pin is not None

    def test_thaw_file_not_found_in_cold(self, db_session, tmp_path):
        """Test error when file is missing from cold storage."""
        record = FileRecord(
            path_id=1,
            original_path="/tmp/hot.txt",
            cold_storage_path="/tmp/missing_cold.txt",
            file_size=0,
            operation_type=OperationType.MOVE
        )
        db_session.add(record)
        db_session.commit()
        
        success, error = FileThawer.thaw_file(record, db=db_session)
        
        assert success is False
        assert "not found" in error.lower()
