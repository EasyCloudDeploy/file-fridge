import pytest
import os
from datetime import datetime, timezone
from pathlib import Path

from app.models import MonitoredPath, FileInventory, OperationType, StorageType, ColdStorageLocation
from app.services.file_reconciliation import FileReconciliation


@pytest.mark.unit
class TestFileReconciliation:
    def test_reconcile_missing_symlinks_success(self, db_session, tmp_path, file_inventory_factory, storage_location):
        """Test recreating missing symlinks for cold storage files."""
        hot_dir = tmp_path / "hot_recon"
        hot_dir.mkdir()
        cold_dir = tmp_path / "cold_recon"
        cold_dir.mkdir()
        
        cold_file = cold_dir / "test.txt"
        cold_file.write_text("data")
        
        # Ensure the cold storage location path matches our temp dir
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()
        
        # Create inventory
        inv = file_inventory_factory(
            path=str(cold_file), 
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location
        )
        path = db_session.get(MonitoredPath, inv.path_id)
        path.operation_type = OperationType.SYMLINK
        path.source_path = str(hot_dir)
        db_session.commit()
        
        # Symlink is missing in hot_dir
        expected_symlink = hot_dir / "test.txt"
        assert not expected_symlink.exists()
        
        stats = FileReconciliation.reconcile_missing_symlinks(path, db_session)
        
        assert stats["symlinks_created"] == 1
        assert expected_symlink.is_symlink()
        assert expected_symlink.resolve() == cold_file

    def test_reconcile_symlinks_skip_non_symlink_path(self, db_session, monitored_path_factory):
        """Test skipping reconciliation if path is not using SYMLINK operation."""
        path = monitored_path_factory("Move Path", "/tmp/hot")
        path.operation_type = OperationType.MOVE
        db_session.commit()
        
        stats = FileReconciliation.reconcile_missing_symlinks(path, db_session)
        assert stats["symlinks_checked"] == 0

    def test_reconcile_symlinks_already_exists(self, db_session, tmp_path, file_inventory_factory, storage_location):
        """Test skipping reconciliation if symlink already exists and is correct."""
        hot_dir = tmp_path / "hot_exists"
        hot_dir.mkdir()
        cold_dir = tmp_path / "cold_exists"
        cold_dir.mkdir()
        
        cold_file = cold_dir / "test.txt"
        cold_file.write_text("data")
        
        symlink_path = hot_dir / "test.txt"
        symlink_path.symlink_to(cold_file)
        
        # Ensure the cold storage location path matches our temp dir
        storage_location.path = str(cold_dir)
        db_session.add(storage_location)
        db_session.commit()
        
        inv = file_inventory_factory(
            path=str(cold_file), 
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location
        )
        path = db_session.get(MonitoredPath, inv.path_id)
        path.operation_type = OperationType.SYMLINK
        path.source_path = str(hot_dir)
        db_session.commit()
        
        stats = FileReconciliation.reconcile_missing_symlinks(path, db_session)
        assert stats["symlinks_skipped"] == 1
        assert stats["symlinks_created"] == 0

    def test_verify_cold_storage_tracking_success(self, db_session, tmp_path, monitored_path_factory):
        """Test verifying that files in cold storage are tracked in DB."""
        cold_dir = tmp_path / "cold_track"
        cold_dir.mkdir()
        f1 = cold_dir / "tracked.txt"
        f1.write_text("t")
        f2 = cold_dir / "untracked.txt"
        f2.write_text("u")
        
        path = monitored_path_factory("Track Path", "/tmp/hot")
        loc = path.storage_locations[0]
        loc.path = str(cold_dir)
        
        # Track f1 in DB
        inv = FileInventory(
            path_id=path.id, 
            file_path=str(f1), 
            file_size=1, 
            file_mtime=datetime.fromtimestamp(os.path.getmtime(f1), tz=timezone.utc),
            storage_type=StorageType.COLD, 
            status="active"
        )
        db_session.add(inv)
        db_session.commit()
        
        stats = FileReconciliation.verify_cold_storage_tracking(path, db_session)
        
        assert stats["files_checked"] == 2
        assert stats["files_tracked"] == 1
        assert stats["files_untracked"] == 1

    def test_verify_cold_storage_tracking_path_not_exists(self, db_session, monitored_path_factory):
        """Test verification when cold storage path is missing."""
        path = monitored_path_factory("Missing Path", "/tmp/hot")
        loc = path.storage_locations[0]
        loc.path = "/non/existent/cold/path"
        db_session.commit()
        
        stats = FileReconciliation.verify_cold_storage_tracking(path, db_session)
        assert stats["files_checked"] == 0
