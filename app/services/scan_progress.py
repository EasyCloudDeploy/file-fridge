"""Real-time scan progress tracking for UI feedback."""
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class FileOperation:
    """Represents a file operation in progress."""
    file_name: str
    operation: str  # "move_to_cold", "move_to_hot", "copy"
    bytes_total: int
    bytes_transferred: int = 0

    @property
    def percent(self) -> int:
        """Calculate percentage complete."""
        if self.bytes_total == 0:
            return 100
        return min(100, int((self.bytes_transferred / self.bytes_total) * 100))


@dataclass
class ScanProgress:
    """Represents the progress of a scan operation."""
    scan_id: str
    path_id: int
    status: str  # "running", "completed", "failed"
    started_at: str
    completed_at: Optional[str] = None
    total_files: int = 0
    files_processed: int = 0
    files_moved_to_cold: int = 0
    files_moved_to_hot: int = 0
    files_skipped: int = 0
    current_operations: List[FileOperation] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert FileOperation objects to dicts
        data['current_operations'] = [
            {
                'file_name': op.file_name,
                'operation': op.operation,
                'bytes_total': op.bytes_total,
                'bytes_transferred': op.bytes_transferred,
                'percent': op.percent
            }
            for op in self.current_operations
        ]
        # Add progress summary
        data['progress'] = {
            'total_files': self.total_files,
            'files_processed': self.files_processed,
            'files_moved_to_cold': self.files_moved_to_cold,
            'files_moved_to_hot': self.files_moved_to_hot,
            'files_skipped': self.files_skipped,
            'percent': self.percent_complete
        }
        return data

    @property
    def percent_complete(self) -> int:
        """Calculate overall percentage complete."""
        if self.total_files == 0:
            return 0
        return min(100, int((self.files_processed / self.total_files) * 100))


class ScanProgressManager:
    """
    Thread-safe manager for tracking scan progress in memory.

    Provides real-time progress updates for file operations during scans.
    Use the module-level `scan_progress_manager` instance.
    """

    def __init__(self):
        """Initialize the progress manager."""
        self._lock = threading.Lock()
        self._scans: Dict[int, ScanProgress] = {}
        self._scans_by_id: Dict[str, ScanProgress] = {}
        self._cleanup_thread = None
        self._cleanup_interval = 300
        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """Start background thread to cleanup old completed scans."""
        def cleanup_worker():
            while True:
                try:
                    time.sleep(self._cleanup_interval)
                    self._cleanup_old_scans()
                except Exception as e:
                    logger.error(f"Error in cleanup thread: {e}")

        self._cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        self._cleanup_thread.start()
        logger.info("Scan progress cleanup thread started")

    def _cleanup_old_scans(self):
        """Remove completed scans older than cleanup interval."""
        with self._lock:
            current_time = time.time()
            to_remove = []

            for scan_id, progress in self._scans_by_id.items():
                if progress.status in ["completed", "failed"] and progress.completed_at:
                    completed_time = datetime.fromisoformat(progress.completed_at).timestamp()
                    if current_time - completed_time > self._cleanup_interval:
                        to_remove.append(scan_id)

            for scan_id in to_remove:
                progress = self._scans_by_id.pop(scan_id, None)
                if progress:
                    self._scans.pop(progress.path_id, None)
                    logger.debug(f"Cleaned up old scan: {scan_id}")

    def start_scan(self, path_id: int, total_files: int = 0) -> str:
        """
        Start tracking a new scan operation.

        Args:
            path_id: The monitored path ID being scanned
            total_files: Total number of files to process

        Returns:
            scan_id: Unique identifier for this scan
        """
        with self._lock:
            scan_id = str(uuid.uuid4())
            progress = ScanProgress(
                scan_id=scan_id,
                path_id=path_id,
                status="running",
                started_at=datetime.now().isoformat(),
                total_files=total_files
            )

            self._scans[path_id] = progress
            self._scans_by_id[scan_id] = progress

            logger.info(f"Started scan tracking: {scan_id} for path {path_id}, {total_files} files")
            return scan_id

    def update_total_files(self, path_id: int, total_files: int):
        """Update the total file count (useful when count is determined during scan)."""
        with self._lock:
            if path_id in self._scans:
                self._scans[path_id].total_files = total_files

    def start_file_operation(self, path_id: int, file_name: str, operation: str, file_size: int):
        """
        Start tracking a file operation.

        Args:
            path_id: The monitored path ID
            file_name: Name of the file being operated on
            operation: Type of operation (move_to_cold, move_to_hot, copy)
            file_size: Size of the file in bytes
        """
        with self._lock:
            if path_id not in self._scans:
                logger.warning(f"No active scan for path {path_id}, starting one")
                self.start_scan(path_id)

            progress = self._scans[path_id]

            # Add to current operations (limit to 5 most recent)
            file_op = FileOperation(
                file_name=file_name,
                operation=operation,
                bytes_total=file_size,
                bytes_transferred=0
            )
            progress.current_operations.append(file_op)
            if len(progress.current_operations) > 5:
                progress.current_operations.pop(0)

    def update_file_progress(self, path_id: int, file_name: str, bytes_transferred: int):
        """
        Update progress for a file operation.

        Args:
            path_id: The monitored path ID
            file_name: Name of the file
            bytes_transferred: Number of bytes transferred so far
        """
        with self._lock:
            if path_id not in self._scans:
                return

            progress = self._scans[path_id]

            # Find the operation for this file
            for op in progress.current_operations:
                if op.file_name == file_name:
                    op.bytes_transferred = bytes_transferred
                    break

    def complete_file_operation(self, path_id: int, file_name: str, operation: str, success: bool = True, error: Optional[str] = None):
        """
        Mark a file operation as complete.

        Args:
            path_id: The monitored path ID
            file_name: Name of the file
            operation: Type of operation that completed
            success: Whether the operation succeeded
            error: Error message if operation failed
        """
        with self._lock:
            if path_id not in self._scans:
                return

            progress = self._scans[path_id]

            # Remove from current operations
            progress.current_operations = [
                op for op in progress.current_operations
                if op.file_name != file_name
            ]

            # Update counters
            progress.files_processed += 1

            if success:
                if operation == "move_to_cold":
                    progress.files_moved_to_cold += 1
                elif operation == "move_to_hot":
                    progress.files_moved_to_hot += 1
                elif operation == "skip":
                    progress.files_skipped += 1
            else:
                if error:
                    progress.errors.append(f"{file_name}: {error}")

    def finish_scan(self, path_id: int, status: str = "completed"):
        """
        Mark a scan as complete.

        Args:
            path_id: The monitored path ID
            status: Final status ("completed" or "failed")
        """
        with self._lock:
            if path_id not in self._scans:
                return

            progress = self._scans[path_id]
            progress.status = status
            progress.completed_at = datetime.now().isoformat()
            progress.current_operations = []  # Clear any pending operations

            logger.info(f"Scan {progress.scan_id} finished with status: {status}")

    def get_progress(self, path_id: int) -> Optional[dict]:
        """
        Get current progress for a path.

        Args:
            path_id: The monitored path ID

        Returns:
            Progress dictionary or None if no active scan
        """
        with self._lock:
            if path_id not in self._scans:
                return None
            return self._scans[path_id].to_dict()

    def get_progress_by_scan_id(self, scan_id: str) -> Optional[dict]:
        """
        Get progress by scan ID.

        Args:
            scan_id: The scan identifier

        Returns:
            Progress dictionary or None if not found
        """
        with self._lock:
            if scan_id not in self._scans_by_id:
                return None
            return self._scans_by_id[scan_id].to_dict()


# Global singleton instance
scan_progress_manager = ScanProgressManager()
