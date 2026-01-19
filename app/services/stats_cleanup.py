"""Statistics cleanup service for data retention management."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    RemoteTransferJob,
    TransferStatus,
)

logger = logging.getLogger(__name__)


class StatsCleanupService:
    """Service for cleaning up old statistics data."""

    def _cleanup_temp_files_in_dir(
        self, directory: Path, cutoff_time: datetime
    ) -> tuple[int, int]:
        """
        Scan a directory and clean up old .fftmp files.

        Returns:
            A tuple of (deleted_count, total_size_freed).
        """
        if not directory.exists():
            return 0, 0

        deleted_count = 0
        total_size_freed = 0

        for temp_file in directory.rglob("*.fftmp"):
            try:
                stat = temp_file.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)

                if mtime < cutoff_time:
                    total_size_freed += stat.st_size
                    temp_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted orphaned temp file: {temp_file}")
            except Exception as e:
                logger.warning(f"Failed to process temp file {temp_file}: {e}")

        return deleted_count, total_size_freed

    def cleanup_orphaned_temp_files(self, db: Session) -> dict:
        """
        Clean up orphaned .fftmp files older than 24 hours.

        These are temporary files from incomplete or failed remote transfers.

        Args:
            db: Database session

        Returns:
            dict: Statistics about cleanup operation
        """
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
            total_deleted_count = 0
            total_size_freed = 0

            paths = db.query(MonitoredPath).filter(MonitoredPath.enabled).all()

            for path in paths:
                # Clean up source path
                deleted, size = self._cleanup_temp_files_in_dir(
                    Path(path.source_path), cutoff_time
                )
                total_deleted_count += deleted
                total_size_freed += size

                # Clean up storage locations
                for location in path.storage_locations:
                    deleted, size = self._cleanup_temp_files_in_dir(
                        Path(location.path), cutoff_time
                    )
                    total_deleted_count += deleted
                    total_size_freed += size

            logger.info(
                f"Orphaned temp file cleanup: {total_deleted_count} files, {total_size_freed} bytes freed"
            )

            return {
                "success": True,
                "files_deleted": total_deleted_count,
                "bytes_freed": total_size_freed,
                "cutoff_time": cutoff_time.isoformat(),
                "message": f"Deleted {total_deleted_count} orphaned temporary files ({total_size_freed / 1024 / 1024:.2f} MB)",
            }
        except Exception as e:
            logger.exception("Error during orphaned temp file cleanup")
            return {
                "success": False,
                "files_deleted": 0,
                "error": str(e),
                "message": "Orphaned temp file cleanup failed",
            }

    def detect_zombie_transfers(self, db: Session) -> dict:
        """
        Detect and recover zombie transfers (stuck in IN_PROGRESS >1 hour without progress).

        Args:
            db: Database session

        Returns:
            dict: Statistics about zombie detection
        """
        try:
            # Find transfers stuck in IN_PROGRESS for >1 hour without progress
            stale_threshold = datetime.now(timezone.utc) - timedelta(hours=1)
            zombies = (
                db.query(RemoteTransferJob)
                .filter(
                    RemoteTransferJob.status == TransferStatus.IN_PROGRESS,
                    RemoteTransferJob.start_time < stale_threshold,
                    RemoteTransferJob.progress < 100,  # Not completed
                )
                .all()
            )

            recovered_count = 0
            for zombie in zombies:
                # Mark as failed
                zombie.status = TransferStatus.FAILED
                zombie.error_message = "Transfer exceeded timeout - marked as zombie"
                zombie.end_time = datetime.now(timezone.utc)
                zombie.retry_count += 1
                recovered_count += 1
                logger.info(
                    f"Recovered zombie transfer {zombie.id}: stuck for "
                    f"{datetime.now(timezone.utc) - zombie.start_time}"
                )

            db.commit()

            logger.info(
                f"Zombie transfer detection: {recovered_count} transfers recovered from zombie state"
            )

            return {
                "success": True,
                "zombies_recovered": recovered_count,
                "stale_threshold": stale_threshold.isoformat(),
                "message": f"Recovered {recovered_count} zombie transfers",
            }
        except Exception as e:
            logger.exception("Error during zombie transfer detection")
            return {
                "success": False,
                "zombies_recovered": 0,
                "error": str(e),
                "message": "Zombie transfer detection failed",
            }

    def cleanup_old_records(self, db: Session) -> dict:
        """
        Delete FileRecord and old MISSING FileInventory entries older than the retention period.

        Args:
            db: Database session

        Returns:
            dict: Statistics about the cleanup operation
        """
        try:
            # Calculate cutoff date
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=settings.stats_retention_days)

            logger.info(
                f"Starting stats cleanup: deleting records older than {cutoff_date} "
                f"(retention period: {settings.stats_retention_days} days)"
            )

            # 1. Clean up FileRecord (Audit Log)
            records_deleted = (
                db.query(FileRecord)
                .filter(FileRecord.moved_at < cutoff_date)
                .delete(synchronize_session=False)
            )

            # 2. Clean up MISSING/DELETED FileInventory entries
            # These are records that haven't been seen for a long time
            inventory_deleted = (
                db.query(FileInventory)
                .filter(
                    FileInventory.status.in_([FileStatus.MISSING, FileStatus.DELETED]),
                    FileInventory.last_seen < cutoff_date,
                )
                .delete(synchronize_session=False)
            )

            # 3. Clean up old RemoteTransferJob entries
            # Delete completed/failed/cancelled jobs older than the retention period
            # For jobs with end_time, use that; for jobs without end_time (orphaned), use start_time
            transfers_deleted = (
                db.query(RemoteTransferJob)
                .filter(
                    RemoteTransferJob.status.in_(
                        [
                            TransferStatus.COMPLETED,
                            TransferStatus.FAILED,
                            TransferStatus.CANCELLED,
                        ]
                    ),
                    or_(
                        RemoteTransferJob.end_time < cutoff_date,
                        # Handle orphaned transfers without end_time
                        (
                            RemoteTransferJob.end_time.is_(None)
                            & (RemoteTransferJob.start_time < cutoff_date)
                        ),
                    ),
                )
                .delete(synchronize_session=False)
            )

            db.commit()

            logger.info(
                f"Deleted {records_deleted} old FileRecord entries, {inventory_deleted} missing inventory entries, "
                f"and {transfers_deleted} remote transfer jobs"
            )

            return {
                "success": True,
                "records_deleted": records_deleted,
                "inventory_deleted": inventory_deleted,
                "transfers_deleted": transfers_deleted,
                "cutoff_date": cutoff_date.isoformat(),
                "message": f"Successfully deleted {records_deleted + inventory_deleted + transfers_deleted} records",
            }

        except Exception as e:
            logger.exception("Error during stats cleanup")
            db.rollback()
            return {
                "success": False,
                "records_deleted": 0,
                "error": str(e),
                "message": "Cleanup failed",
            }


def cleanup_old_stats_job_func():
    """
    Module-level function for scheduled stats cleanup.
    This is used by APScheduler to avoid serialization issues.
    """
    db = SessionLocal()
    try:
        service = StatsCleanupService()

        # Clean up orphaned temp files
        temp_cleanup = service.cleanup_orphaned_temp_files(db)
        logger.info(f"Orphaned temp file cleanup completed: {temp_cleanup}")

        # Detect and recover zombie transfers
        zombie_detection = service.detect_zombie_transfers(db)
        logger.info(f"Zombie transfer detection completed: {zombie_detection}")

        # Clean up old database records
        result = service.cleanup_old_records(db)
        logger.info(f"Stats cleanup completed: {result}")
    except Exception:
        logger.exception("Error in scheduled stats cleanup")
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning(f"Error closing database session: {e}")


# Create global instance
stats_cleanup_service = StatsCleanupService()
