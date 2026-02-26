import pytest
import time
from unittest.mock import MagicMock

from app.models import RequestNonce, MonitoredPath, ColdStorageLocation, FileInventory, StorageType
from app.services.scheduler import (
    cleanup_old_nonces_job_func, 
    rotate_remote_code_job_func, 
    scan_path_job_func,
    encrypt_location_job_func,
    decrypt_location_job_func
)


@pytest.mark.unit
class TestSchedulerService:
    @pytest.fixture(autouse=True)
    def mock_scheduler_session(self, monkeypatch, db_session):
        """Mock SchedulerSessionLocal to use the test db_session."""
        # Prevent the job from actually closing the session
        monkeypatch.setattr(db_session, "close", lambda: None)
        
        mock_session_factory = MagicMock(return_value=db_session)
        monkeypatch.setattr("app.services.scheduler.SchedulerSessionLocal", mock_session_factory)
        return mock_session_factory

    def test_cleanup_old_nonces_job(self, db_session):
        """Test the nonce cleanup job function."""
        now = int(time.time())
        old_nonce = RequestNonce(nonce="old-nonce", fingerprint="fp1", timestamp=now - 1000)
        new_nonce = RequestNonce(nonce="new-nonce", fingerprint="fp2", timestamp=now)
        db_session.add_all([old_nonce, new_nonce])
        db_session.commit()
        
        cleanup_old_nonces_job_func()
        
        # Verify old one is gone, new one remains
        remaining = db_session.query(RequestNonce).all()
        assert len(remaining) == 1
        assert remaining[0].nonce == "new-nonce"

    def test_rotate_remote_code_job(self, monkeypatch):
        """Test the remote code rotation job function."""
        from app.utils.remote_auth import remote_auth
        mock_rotate = MagicMock()
        monkeypatch.setattr(remote_auth, "rotate_code", mock_rotate)
        
        rotate_remote_code_job_func()
        assert mock_rotate.called

    def test_scan_path_job_not_found(self):
        """Test scan job with non-existent path."""
        # Should not raise exception
        scan_path_job_func(9999)

    def test_scan_path_job_success(self, db_session, monitored_path_factory, monkeypatch):
        """Test the path scan job function."""
        path = monitored_path_factory("Scan Job Path", "/tmp/hot_job")
        
        from app.services.file_workflow_service import file_workflow_service
        mock_process = MagicMock(return_value={"files_moved": 5, "bytes_saved": 500, "errors": []})
        monkeypatch.setattr(file_workflow_service, "process_path", mock_process)
        
        scan_path_job_func(path.id)
        assert mock_process.called

    def test_encrypt_location_job(self, db_session, storage_location, file_inventory_factory, monkeypatch):
        """Test the bulk encryption job function."""
        # Setup files in location
        storage_location.is_encrypted = False
        db_session.commit()
        
        inv = file_inventory_factory(
            path="/tmp/cold/f1.txt", 
            storage_type=StorageType.COLD, 
            is_encrypted=False,
            cold_storage_location=storage_location
        )
        
        # Mock file encryption service
        from app.services.encryption_service import file_encryption_service
        monkeypatch.setattr(file_encryption_service, "encrypt_file", MagicMock())
        
        # Mock Path.exists and unlink
        from pathlib import Path
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "unlink", MagicMock())
        
        encrypt_location_job_func(storage_location.id)
        
        db_session.refresh(inv)
        assert inv.is_encrypted is True
        db_session.refresh(storage_location)
        assert storage_location.encryption_status == "encrypted"

    def test_decrypt_location_job(self, db_session, storage_location, file_inventory_factory, monkeypatch):
        """Test the bulk decryption job function."""
        storage_location.is_encrypted = True
        db_session.commit()
        
        inv = file_inventory_factory(
            path="/tmp/cold/f1.txt.ffenc", 
            storage_type=StorageType.COLD, 
            is_encrypted=True,
            cold_storage_location=storage_location
        )
        
        from app.services.encryption_service import file_encryption_service
        monkeypatch.setattr(file_encryption_service, "decrypt_file", MagicMock())
        
        from pathlib import Path
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "unlink", MagicMock())
        
        decrypt_location_job_func(storage_location.id)
        
        db_session.refresh(inv)
        assert inv.is_encrypted is False
        db_session.refresh(storage_location)
        assert storage_location.encryption_status == "none"
