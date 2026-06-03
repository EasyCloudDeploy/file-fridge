import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models import (
    FileInventory,
    FileRecord,
    FileStatus,
    OperationType,
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

        db_session.add_all([old_record, new_record])
        db_session.commit()

        stats = stats_cleanup_service.cleanup_old_records(db_session)

        assert stats["records_deleted"] == 1
        assert stats["inventory_deleted"] == 1
        assert stats["transfers_deleted"] == 0

        # Verify remaining
        assert db_session.query(FileRecord).count() == 1
        assert db_session.query(FileInventory).filter_by(status=FileStatus.ACTIVE).count() == 1

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
