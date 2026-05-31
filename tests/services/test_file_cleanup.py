from datetime import datetime, timezone, timedelta
import pytest
from pathlib import Path

from app.models import FileInventory, FileStatus, StorageType
from app.services.file_cleanup import FileCleanup


@pytest.mark.unit
class TestFileCleanupService:
    def test_cleanup_symlink_inventory_entries(self, db_session, tmp_path, file_inventory_factory):
        """Test symlink and suspected symlink cleanup with stale/fresh durations."""
        # 1. A real symlink entry (exists in filesystem and is a symlink)
        target = tmp_path / "target.txt"
        target.write_text("hello")
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        inv_link = file_inventory_factory(
            path=str(link),
            storage_type=StorageType.HOT,
            status=FileStatus.ACTIVE
        )

        # 2. A normal active file (exists, not a symlink)
        normal_file = tmp_path / "normal.txt"
        normal_file.write_text("hello world")
        inv_normal = file_inventory_factory(
            path=str(normal_file),
            storage_type=StorageType.HOT,
            status=FileStatus.ACTIVE
        )

        # 3. A stale suspected symlink (missing, tiny size < 200, has checksum, last_seen > 24 hours ago)
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=25)
        inv_stale_suspect = FileInventory(
            path_id=inv_normal.path_id,
            file_path=str(tmp_path / "stale_suspect.txt"),
            storage_type=StorageType.HOT,
            status=FileStatus.MISSING,
            file_size=50,
            checksum="some_hash",
            last_seen=stale_time,
            file_mtime=datetime.now(tz=timezone.utc)
        )
        db_session.add(inv_stale_suspect)

        # 4. A fresh suspected symlink (missing, tiny size < 200, has checksum, last_seen < 24 hours ago)
        fresh_time = datetime.now(tz=timezone.utc) - timedelta(hours=10)
        inv_fresh_suspect = FileInventory(
            path_id=inv_normal.path_id,
            file_path=str(tmp_path / "fresh_suspect.txt"),
            storage_type=StorageType.HOT,
            status=FileStatus.MISSING,
            file_size=50,
            checksum="some_hash",
            last_seen=fresh_time,
            file_mtime=datetime.now(tz=timezone.utc)
        )
        db_session.add(inv_fresh_suspect)
        db_session.commit()

        # Run the cleanup
        results = FileCleanup.cleanup_symlink_inventory_entries(db_session)

        # Assert results
        assert results["removed"] == 2  # The real symlink + the stale suspected symlink
        assert results["checked"] > 0

        # Verify which entries are still in the database
        db_ids = [entry.id for entry in db_session.query(FileInventory).all()]
        assert inv_link.id not in db_ids
        assert inv_stale_suspect.id not in db_ids
        assert inv_normal.id in db_ids
        assert inv_fresh_suspect.id in db_ids
