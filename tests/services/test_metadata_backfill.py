import pytest
from pathlib import Path

from app.models import FileInventory
from app.services.metadata_backfill import MetadataBackfillService


@pytest.mark.unit
class TestMetadataBackfillService:
    def test_backfill_all_success(self, db_session, tmp_path, file_inventory_factory):
        """Test backfilling metadata for all files in inventory."""
        # Create files on disk
        f1 = tmp_path / "test1.txt"
        f1.write_text("content1")
        f2 = tmp_path / "test2.jpg"
        # Minimum JPEG header to be recognized
        f2.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00")
        
        # Create inventory entries missing metadata
        inv1 = file_inventory_factory(path=str(f1), file_extension=None, mime_type=None, checksum=None)
        inv2 = file_inventory_factory(path=str(f2), file_extension=None, mime_type=None, checksum=None)
        
        service = MetadataBackfillService(db_session)
        stats = service.backfill_all()
        
        assert stats["files_processed"] == 2
        assert stats["total_files"] == 2
        
        db_session.refresh(inv1)
        assert inv1.file_extension == ".txt"
        assert inv1.mime_type == "text/plain"
        assert inv1.checksum is not None
        
        db_session.refresh(inv2)
        assert inv2.file_extension == ".jpg"
        # MIME type detection might vary slightly by OS (image/jpeg or image/pjpeg)
        assert "image/jpeg" in inv2.mime_type
        assert inv2.checksum is not None

    def test_backfill_skipped_missing_file(self, db_session, file_inventory_factory):
        """Test that missing files are skipped during backfill."""
        # Entry for non-existent file
        inv = file_inventory_factory(path="/tmp/nonexistent_backfill.txt", file_extension=None)
        
        service = MetadataBackfillService(db_session)
        stats = service.backfill_all()
        
        assert stats["files_skipped"] == 1
        assert stats["files_processed"] == 0

    def test_backfill_path(self, db_session, tmp_path, file_inventory_factory):
        """Test backfilling metadata for a specific path."""
        f1 = tmp_path / "test1.txt"
        f1.write_text("content1")
        inv1 = file_inventory_factory(path=str(f1), file_extension=None)
        path_id = inv1.path_id
        
        # Another path
        f2 = tmp_path / "test2.txt"
        f2.write_text("content2")
        inv2 = file_inventory_factory(path=str(f2), file_extension=None, path_name="other_path")
        
        service = MetadataBackfillService(db_session)
        stats = service.backfill_path(path_id)
        
        assert stats["total_files"] == 1
        assert stats["files_processed"] == 1
        
        db_session.refresh(inv1)
        assert inv1.file_extension is not None
        db_session.refresh(inv2)
        assert inv2.file_extension is None  # Not processed because different path_id

    def test_backfill_already_filled(self, db_session, tmp_path, file_inventory_factory):
        """Test that files with existing metadata are not re-processed."""
        f1 = tmp_path / "test1.txt"
        f1.write_text("content1")
        inv1 = file_inventory_factory(
            path=str(f1), 
            file_extension=".txt", 
            mime_type="text/plain", 
            checksum="existing-checksum"
        )
        
        service = MetadataBackfillService(db_session)
        # Should find 0 files needing update because filter checks for NULLs
        stats = service.backfill_all()
        
        assert stats["total_files"] == 0
        assert stats["files_processed"] == 0

    def test_backfill_no_checksum_option(self, db_session, tmp_path, file_inventory_factory):
        """Test backfilling without computing checksums."""
        f1 = tmp_path / "test1.txt"
        f1.write_text("content1")
        inv1 = file_inventory_factory(path=str(f1), file_extension=None, checksum=None)
        
        service = MetadataBackfillService(db_session)
        stats = service.backfill_all(compute_checksum=False)
        
        assert stats["files_processed"] == 1
        db_session.refresh(inv1)
        assert inv1.file_extension == ".txt"
        assert inv1.checksum is None  # Should still be None

    def test_backfill_batch_commit_failure(self, db_session, tmp_path, file_inventory_factory, monkeypatch):
        """Test handling of commit failure during backfill."""
        f1 = tmp_path / "test1.txt"
        f1.write_text("content1")
        inv1 = file_inventory_factory(path=str(f1), file_extension=None)
        
        service = MetadataBackfillService(db_session)
        
        # Mock commit to fail
        original_commit = db_session.commit
        def mock_commit():
            raise Exception("Database error")
        monkeypatch.setattr(db_session, "commit", mock_commit)
        
        stats = service.backfill_all(batch_size=1)
        
        assert stats["files_failed"] == 1
        # The service currently increments processed before committing
        assert stats["files_processed"] == 1
