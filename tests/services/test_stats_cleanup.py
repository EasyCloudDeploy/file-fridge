import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models import (
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    RemoteTransferJob,
    TransferStatus,
    StorageType,
)
from app.services.stats_cleanup import stats_cleanup_service


@pytest.mark.unit
class TestStatsCleanupService:
    def test_cleanup_old_records_success(self, db_session, file_inventory_factory):
        """Test cleaning up old statistics records."""
        # Setup: Default retention is 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=31)
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        
        # 1. FileRecords
        old_record = FileRecord(
            original_path="/tmp/old", cold_storage_path="/tmp/cold/old",
            file_size=100, operation_type=OperationType.MOVE, moved_at=cutoff
        )
        new_record = FileRecord(
            original_path="/tmp/new", cold_storage_path="/tmp/cold/new",
            file_size=100, operation_type=OperationType.MOVE, moved_at=recent
        )
        
        # 2. FileInventory (MISSING/DELETED)
        # Use factory to ensure all required fields are set
        old_inv = file_inventory_factory(path="/tmp/missing", status=FileStatus.MISSING, storage_type=StorageType.COLD)
        old_inv.last_seen = cutoff
        
        new_inv = file_inventory_factory(path="/tmp/active", status=FileStatus.ACTIVE, storage_type=StorageType.HOT, path_name="active_path")
        new_inv.last_seen = recent
        
        # 3. RemoteTransferJob
        from app.models import RemoteConnection
        conn = RemoteConnection(
            name="target", url="http://remote", trust_status="TRUSTED"
        )
        db_session.add(conn)
        db_session.commit()
        
        old_job = RemoteTransferJob(
            file_inventory_id=new_inv.id,
            remote_connection_id=conn.id,
            remote_monitored_path_id=1,
            source_path="/tmp/new",
            relative_path="new",
            storage_type=StorageType.HOT,
            status=TransferStatus.COMPLETED, 
            start_time=cutoff, 
            end_time=cutoff,
            total_size=100
        )
        
        db_session.add_all([old_record, new_record, old_job])
        db_session.commit()
        
        stats = stats_cleanup_service.cleanup_old_records(db_session)
        
        assert stats["records_deleted"] == 1
        assert stats["inventory_deleted"] == 1
        assert stats["transfers_deleted"] == 1
        
        # Verify remaining
        assert db_session.query(FileRecord).count() == 1
        assert db_session.query(FileInventory).filter_by(status=FileStatus.ACTIVE).count() == 1

    def test_detect_zombie_transfers_success(self, db_session, file_inventory_factory):
        """Test detecting and recovering zombie transfers."""
        # Use naive datetimes if SQLite is causing issues with offset-aware
        # But app uses timezone.utc, so let's try to be consistent
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        
        inv = file_inventory_factory(path="/tmp/zombie_inv")
        from app.models import RemoteConnection
        conn = RemoteConnection(
            name="zombie-target", url="http://remote-zombie", trust_status="TRUSTED"
        )
        db_session.add(conn)
        db_session.commit()

        zombie = RemoteTransferJob(
            file_inventory_id=inv.id,
            remote_connection_id=conn.id,
            remote_monitored_path_id=1,
            source_path=inv.file_path,
            relative_path="zombie",
            storage_type=StorageType.HOT,
            status=TransferStatus.IN_PROGRESS, 
            start_time=stale_time, 
            progress=45,
            total_size=1000, 
            retry_count=0
        )
        db_session.add(zombie)
        db_session.commit()
        
        stats = stats_cleanup_service.detect_zombie_transfers(db_session)
        
        # If it failed due to TypeError in logging, it would still return success=False in catch
        if not stats["success"]:
            pytest.fail(f"Zombie detection failed: {stats.get('error')}")
            
        assert stats["zombies_recovered"] == 1
        db_session.refresh(zombie)
        assert zombie.status == TransferStatus.FAILED
        assert "zombie" in zombie.error_message.lower()
        assert zombie.retry_count == 1

    def test_cleanup_orphaned_temp_files_success(self, db_session, tmp_path, monitored_path_factory):
        """Test cleaning up orphaned .fftmp files."""
        hot_dir = tmp_path / "hot_tmp"
        hot_dir.mkdir()
        path = monitored_path_factory("Temp Path", str(hot_dir))
        
        # Create an old temp file
        temp_file = hot_dir / "orphaned.fftmp"
        temp_file.write_text("temp data")
        
        # Set mtime back 30 hours
        old_time = (datetime.now(timezone.utc) - timedelta(hours=30)).timestamp()
        os.utime(str(temp_file), (old_time, old_time))
        
        # Create a recent temp file (should stay)
        recent_file = hot_dir / "recent.fftmp"
        recent_file.write_text("recent data")
        
        stats = stats_cleanup_service.cleanup_orphaned_temp_files(db_session)
        
        assert stats["files_deleted"] == 1
        assert not temp_file.exists()
        assert recent_file.exists()

    def test_cleanup_temp_files_in_dir_nonexistent(self):
        """Test cleanup in non-existent directory."""
        deleted, size = stats_cleanup_service._cleanup_temp_files_in_dir(Path("/non/existent/path"), datetime.now(timezone.utc))
        assert deleted == 0
        assert size == 0
