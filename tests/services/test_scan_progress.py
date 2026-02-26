import pytest
import time
from datetime import datetime, timezone, timedelta

from app.services.scan_progress import scan_progress_manager, ScanProgress, FileOperation


@pytest.mark.unit
class TestScanProgressManager:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """Reset the global scan progress manager state."""
        with scan_progress_manager._lock:
            scan_progress_manager._scans.clear()
            scan_progress_manager._scans_by_id.clear()
        yield

    def test_start_scan_success(self):
        """Test starting a new scan."""
        scan_id, started = scan_progress_manager.start_scan(1, total_files=5)
        assert started is True
        assert scan_id is not None
        assert scan_progress_manager.is_scan_running(1) is True
        
        # Start same path again should return existing scan_id and started=False
        scan_id2, started2 = scan_progress_manager.start_scan(1, total_files=10)
        assert scan_id2 == scan_id
        assert started2 is False

    def test_update_total_files(self):
        """Test updating the total files count."""
        scan_progress_manager.start_scan(2, total_files=0)
        scan_progress_manager.update_total_files(2, 50)
        
        progress = scan_progress_manager.get_progress(2)
        assert progress["progress"]["total_files"] == 50

    def test_file_operation_flow(self):
        """Test tracking a single file operation from start to finish."""
        path_id = 3
        scan_progress_manager.start_scan(path_id, total_files=1)
        
        # Start op
        scan_progress_manager.start_file_operation(path_id, "test.dat", "move_to_cold", 1000)
        progress = scan_progress_manager.get_progress(path_id)
        assert len(progress["current_operations"]) == 1
        assert progress["current_operations"][0]["file_name"] == "test.dat"
        
        # Update progress
        scan_progress_manager.update_file_progress(path_id, "test.dat", 400)
        progress = scan_progress_manager.get_progress(path_id)
        assert progress["current_operations"][0]["percent"] == 40
        
        # Complete
        scan_progress_manager.complete_file_operation(path_id, "test.dat", "move_to_cold", success=True)
        progress = scan_progress_manager.get_progress(path_id)
        assert len(progress["current_operations"]) == 0
        assert progress["progress"]["files_processed"] == 1
        assert progress["progress"]["files_moved_to_cold"] == 1
        assert progress["progress"]["percent"] == 100

    def test_file_operation_failure(self):
        """Test tracking a failed file operation."""
        path_id = 4
        scan_progress_manager.start_scan(path_id, total_files=1)
        
        scan_progress_manager.start_file_operation(path_id, "fail.txt", "copy", 100)
        scan_progress_manager.complete_file_operation(path_id, "fail.txt", "copy", success=False, error="Access denied")
        
        progress = scan_progress_manager.get_progress(path_id)
        assert len(progress["errors"]) == 1
        assert "Access denied" in progress["errors"][0]

    def test_finish_scan(self):
        """Test finishing a scan."""
        path_id = 5
        scan_progress_manager.start_scan(path_id)
        scan_progress_manager.finish_scan(path_id, status="completed")
        
        assert scan_progress_manager.is_scan_running(path_id) is False
        progress = scan_progress_manager.get_progress(path_id)
        assert progress["status"] == "completed"
        assert progress["completed_at"] is not None

    def test_get_progress_by_scan_id(self):
        """Test getting progress by scan ID instead of path ID."""
        scan_id, _ = scan_progress_manager.start_scan(6)
        
        progress = scan_progress_manager.get_progress_by_scan_id(scan_id)
        assert progress is not None
        assert progress["scan_id"] == scan_id
        
        assert scan_progress_manager.get_progress_by_scan_id("non-existent") is None

    def test_cleanup_old_scans(self):
        """Test the internal cleanup of old scan records."""
        path_id = 7
        scan_id, _ = scan_progress_manager.start_scan(path_id)
        
        # Manually complete it and set old completion time
        with scan_progress_manager._lock:
            progress = scan_progress_manager._scans[path_id]
            progress.status = "completed"
            # Set completion time 1 hour ago
            old_time = datetime.now(timezone.utc) - timedelta(hours=1)
            progress.completed_at = old_time.isoformat()
            
            # Set cleanup interval to 30 mins
            scan_progress_manager._cleanup_interval = 1800
            
        scan_progress_manager._cleanup_old_scans()
        
        assert scan_progress_manager.get_progress(path_id) is None
