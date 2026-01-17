"""Statistics cleanup service for data retention management."""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import FileInventory, FileRecord, FileStatus

logger = logging.getLogger(__name__)


class StatsCleanupService:
    """Service for cleaning up old statistics data."""

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
            cutoff_date = datetime.now() - timedelta(days=settings.stats_retention_days)

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

            db.commit()

            logger.info(
                f"Deleted {records_deleted} old FileRecord entries and {inventory_deleted} missing inventory entries"
            )

            return {
                "success": True,
                "records_deleted": records_deleted,
                "inventory_deleted": inventory_deleted,
                "cutoff_date": cutoff_date.isoformat(),
                "message": f"Successfully deleted {records_deleted + inventory_deleted} records",
            }

        except Exception as e:
            logger.exception(f"Error during stats cleanup: {e}")
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
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        service = StatsCleanupService()
        result = service.cleanup_old_records(db)
        logger.info(f"Stats cleanup completed: {result}")
    except Exception as e:
        logger.exception(f"Error in scheduled stats cleanup: {e}")
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning(f"Error closing database session: {e}")


# Create global instance
stats_cleanup_service = StatsCleanupService()
