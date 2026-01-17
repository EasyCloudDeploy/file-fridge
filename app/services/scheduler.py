import logging
import shutil
import time
import traceback

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine
from app.models import MonitoredPath
from app.services.file_workflow_service import file_workflow_service
from app.services.notification_events import (
    LowDiskSpaceData,
    NotificationEvent,
    SyncErrorData,
    SyncSuccessData,
)
from app.services.notification_service import notification_service
from app.services.stats_cleanup import cleanup_old_stats_job_func

logger = logging.getLogger(__name__)

# Create a separate session factory for scheduler operations
# This prevents scheduler DB operations from interfering with API requests
SchedulerSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class SchedulerService:
    """Manages scheduled file scans."""

    def __init__(self):
        """Initialize scheduler."""
        # Use SQLite jobstore - APScheduler needs a separate table
        # Use a separate database file for jobstore to avoid conflicts
        db_url = str(engine.url)
        if db_url.startswith("sqlite:///"):
            # Use a separate database file for scheduler jobs
            jobstore_url = db_url.replace(".db", "_scheduler.db")
        else:
            jobstore_url = db_url
        jobstore = SQLAlchemyJobStore(url=jobstore_url)

        self.scheduler = BackgroundScheduler(
            jobstores={"default": jobstore},
            executors={"default": ThreadPoolExecutor(5)},
            job_defaults={
                "coalesce": True,  # Skip overlapping jobs
                "max_instances": 1,  # Only one instance per job
                "misfire_grace_time": 30,  # Allow 30 seconds grace time for missed jobs
            },
        )

    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            try:
                self.scheduler.start()
                logger.info("Scheduler started")
                # Small delay to ensure scheduler is fully started
                import time

                time.sleep(0.1)
                self._load_existing_jobs()
                self._add_stats_cleanup_job()
            except Exception as e:
                logger.exception(f"Error starting scheduler: {e}")
                # Try to clean up
                try:
                    if self.scheduler.running:
                        self.scheduler.shutdown(wait=False)
                except:
                    pass
                raise

    def stop(self):
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            try:
                # Shutdown gracefully, waiting for running jobs to complete
                self.scheduler.shutdown(wait=True)
                logger.info("Scheduler stopped gracefully")
            except Exception as e:
                logger.warning(f"Error during scheduler shutdown: {e}")
                try:
                    # Force shutdown if graceful shutdown fails
                    self.scheduler.shutdown(wait=False)
                    logger.info("Scheduler force-stopped")
                except Exception as e2:
                    logger.exception(f"Error during forced scheduler shutdown: {e2}")

    def _load_existing_jobs(self):
        """Load existing monitored paths as scheduled jobs."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping job loading")
            return

        db = SchedulerSessionLocal()
        try:
            paths = db.query(MonitoredPath).filter(MonitoredPath.enabled).all()
            logger.info(f"Loading {len(paths)} enabled paths as scheduled jobs")
            for path in paths:
                try:
                    self.add_path_job(path)
                except Exception as e:
                    logger.exception(f"Error loading job for path {path.id}: {e}")
        except Exception as e:
            logger.exception(f"Error loading existing jobs: {e}")
        finally:
            db.close()

    def add_path_job(self, path: MonitoredPath):
        """Add or update a scheduled job for a path."""
        if not self.scheduler.running:
            logger.warning(f"Scheduler not running, skipping job addition for path {path.id}")
            return

        job_id = f"scan_path_{path.id}"

        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            if path.enabled:
                # Use a module-level function instead of instance method to avoid serialization issues
                self.scheduler.add_job(
                    scan_path_job_func,
                    "interval",
                    seconds=path.check_interval_seconds,
                    id=job_id,
                    args=[path.id],
                    replace_existing=True,
                )
                logger.info(f"Added scheduled job for path {path.id} ({path.name})")
        except Exception as e:
            logger.exception(f"Error adding job for path {path.id}: {e}")

    def remove_path_job(self, path_id: int):
        """Remove scheduled job for a path."""
        job_id = f"scan_path_{path_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed scheduled job for path {path_id}")

    def trigger_scan(self, path_id: int):
        """Manually trigger a scan for a path."""
        scan_path_job_func(path_id)

    def _scan_path_job(self, path_id: int):
        """Job function to scan a path (kept for backward compatibility, but use scan_path_job_func instead)."""
        scan_path_job_func(path_id)

    def _add_stats_cleanup_job(self):
        """Add scheduled job for stats cleanup (runs daily at 2 AM)."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping stats cleanup job addition")
            return

        job_id = "stats_cleanup"
        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Schedule to run daily at 2 AM
            self.scheduler.add_job(
                cleanup_old_stats_job_func,
                "cron",
                hour=2,
                minute=0,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for daily stats cleanup (runs at 2 AM)")
        except Exception as e:
            logger.exception(f"Error adding stats cleanup job: {e}")


def check_disk_space_and_notify(path: MonitoredPath, db: Session):
    """Check disk space for all cold storage locations and send notifications if low."""
    # TODO: Make this threshold configurable
    low_space_threshold_percent = 90.0

    for location in path.storage_locations:
        try:
            total, used, free = shutil.disk_usage(location.path)
            used_percent = (used / total) * 100

            if used_percent >= low_space_threshold_percent:
                payload = LowDiskSpaceData(
                    storage_name=location.name,
                    storage_path=location.path,
                    free_space_gb=round(free / (1024**3), 2),
                    total_space_gb=round(total / (1024**3), 2),
                    threshold_percent=low_space_threshold_percent,
                )
                notification_service.dispatch_event(
                    db=db,
                    event_type=NotificationEvent.LOW_DISK_SPACE,
                    data=payload,
                )
        except FileNotFoundError:
            logger.warning(
                f"Could not check disk space for {location.name}: path not found at {location.path}"
            )
        except Exception as e:
            logger.exception(f"Error checking disk space for {location.name}: {e}")


def scan_path_job_func(path_id: int):
    """
    Module-level function to scan a path.
    This is used by APScheduler to avoid serialization issues.
    Uses separate database session to avoid interfering with API requests.
    """
    db = SchedulerSessionLocal()
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path or not path.enabled:
        logger.debug(f"Path {path_id} not found or not enabled, skipping scan")
        db.close()
        return

    start_time = time.time()
    try:
        logger.info(f"Starting scan for path {path_id} ({path.name})")
        result = file_workflow_service.process_path(path, db)
        duration = time.time() - start_time

        # Send notifications for individual errors during the scan
        if result["errors"]:
            for error_msg in result["errors"]:
                error_payload = SyncErrorData(path_name=path.name, error_message=error_msg)
                notification_service.dispatch_event(
                    db=db,
                    event_type=NotificationEvent.SYNC_ERROR,
                    data=error_payload,
                )

        # Send success notification
        success_payload = SyncSuccessData(
            path_name=path.name,
            files_scanned=result.get("total_scanned", 0),
            files_moved_to_cold=result.get("files_moved", 0),
            files_thawed_from_cold=0,  # This info is not yet available from the service
            duration_seconds=round(duration, 2),
        )
        notification_service.dispatch_event(
            db=db,
            event_type=NotificationEvent.SYNC_SUCCESS,
            data=success_payload,
        )

        # Check disk space after a successful scan
        check_disk_space_and_notify(path, db)

        logger.info(
            f"Completed scan for path {path_id}: {result['files_moved']} files moved, {len(result['errors'])} errors in {duration:.2f}s"
        )

    except Exception as e:
        duration = time.time() - start_time
        tb_str = traceback.format_exc()
        logger.exception(f"Fatal error scanning path {path_id} after {duration:.2f}s: {e!s}")
        logger.exception(f"Traceback: {tb_str}")

        # Send fatal error notification
        error_payload = SyncErrorData(
            path_name=path.name if path else f"ID {path_id}",
            error_message=f"A fatal error occurred during the scan: {e!s}",
            traceback=tb_str,
        )
        notification_service.dispatch_event(
            db=db,
            event_type=NotificationEvent.SYNC_ERROR,
            data=error_payload,
        )
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning(f"Error closing scheduler database session: {e}")


# Global scheduler instance
scheduler_service = SchedulerService()
