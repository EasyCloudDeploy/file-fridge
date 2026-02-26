import os
from pathlib import Path

import pytest

from app.models import (
    ColdStorageLocation,
    FileInventory,
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
    def test_freeze_file_move_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
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
        record = db_session.query(FileRecord).filter(FileRecord.path_id == monitored_path.id).first()
        assert record is not None
        assert record.cold_storage_path == cold_path

    def test_freeze_file_copy_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
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
        db_session.commit()
        
        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        
        assert success is True, f"Freezer failed: {error}"
        assert hot_file.exists()  # Original stays for COPY
        assert Path(cold_path).exists()

    def test_freeze_file_symlink_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
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
        db_session.commit()
        
        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        
        assert success is True, f"Freezer failed: {error}"
        assert hot_file.is_symlink()
        assert os.path.realpath(hot_file) == os.path.realpath(cold_path)
        
        # For SYMLINK, inv.file_path should NOT change (stays as the symlink path)
        db_session.refresh(inv)
        assert inv.file_path == str(hot_file)

    def test_freeze_file_pin_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
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

    def test_freeze_file_already_in_cold(self, db_session, file_inventory_factory, storage_location):
        """Test that freezing fails if file is already in cold storage."""
        inv = file_inventory_factory(path="/tmp/cold/file.txt", storage_type=StorageType.COLD)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        
        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        
        assert success is False
        assert "not in hot storage" in error.lower()

    def test_freeze_file_not_found_on_disk(self, db_session, file_inventory_factory, storage_location):
        """Test that freezing fails if file is missing from hot storage."""
        inv = file_inventory_factory(path="/tmp/hot/missing.txt", storage_type=StorageType.HOT)
        monitored_path = db_session.get(MonitoredPath, inv.path_id)
        
        success, error, cold_path = FileFreezer.freeze_file(
            inv, monitored_path, storage_location, db=db_session
        )
        
        assert success is False
        assert "not found" in error.lower()

    def test_freeze_file_destination_exists(self, db_session, tmp_path, file_inventory_factory, storage_location):
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

    def test_freeze_file_encrypted_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
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
