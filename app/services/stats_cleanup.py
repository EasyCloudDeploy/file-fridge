"""Statistics cleanup service for data retention management."""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import FileRecord

logger = logging.getLogger(__name__)


class StatsCleanupService:
    """Service for cleaning up old statistics data."""

    def cleanup_old_records(self, db: Session) -> dict:
        """
        Delete FileRecord entries older than the retention period.

        Args:
            db: Database session

        Returns:
            dict: Statistics about the cleanup operation
        """
        try:
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=settings.stats_retention_days)

            logger.info(f"Starting stats cleanup: deleting records older than {cutoff_date} "
                       f"(retention period: {settings.stats_retention_days} days)")

            # Count records to be deleted
            records_to_delete = db.query(FileRecord).filter(
                FileRecord.moved_at < cutoff_date
            ).count()

            if records_to_delete == 0:
                logger.info("No old records to delete")
                return {
                    "success": True,
                    "records_deleted": 0,
                    "cutoff_date": cutoff_date.isoformat(),
                    "message": "No records to delete"
                }

            # Delete old records
            deleted = db.query(FileRecord).filter(
                FileRecord.moved_at < cutoff_date
            ).delete(synchronize_session=False)

            db.commit()

            logger.info(f"Deleted {deleted} old FileRecord entries")

            return {
                "success": True,
                "records_deleted": deleted,
                "cutoff_date": cutoff_date.isoformat(),
                "message": f"Successfully deleted {deleted} records"
            }

        except Exception as e:
            logger.error(f"Error during stats cleanup: {e}")
            db.rollback()
            return {
                "success": False,
                "records_deleted": 0,
                "error": str(e),
                "message": "Cleanup failed"
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
        logger.error(f"Error in scheduled stats cleanup: {e}")
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning(f"Error closing database session: {e}")


# Create global instance
stats_cleanup_service = StatsCleanupService()
