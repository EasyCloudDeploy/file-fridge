"""Scheduler service for periodic file scans."""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from sqlalchemy.orm import Session, sessionmaker
from app.database import engine
from app.models import MonitoredPath
from app.services.file_workflow_service import file_workflow_service
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
        if db_url.startswith('sqlite:///'):
            # Use a separate database file for scheduler jobs
            jobstore_url = db_url.replace('.db', '_scheduler.db')
        else:
            jobstore_url = db_url
        jobstore = SQLAlchemyJobStore(url=jobstore_url)

        self.scheduler = BackgroundScheduler(
            jobstores={'default': jobstore},
            executors={'default': ThreadPoolExecutor(5)},
            job_defaults={
                'coalesce': True,  # Skip overlapping jobs
                'max_instances': 1,  # Only one instance per job
                'misfire_grace_time': 30  # Allow 30 seconds grace time for missed jobs
            }
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
                logger.error(f"Error starting scheduler: {e}")
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
                    logger.error(f"Error during forced scheduler shutdown: {e2}")
    
    def _load_existing_jobs(self):
        """Load existing monitored paths as scheduled jobs."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping job loading")
            return

        db = SchedulerSessionLocal()
        try:
            paths = db.query(MonitoredPath).filter(MonitoredPath.enabled == True).all()
            logger.info(f"Loading {len(paths)} enabled paths as scheduled jobs")
            for path in paths:
                try:
                    self.add_path_job(path)
                except Exception as e:
                    logger.error(f"Error loading job for path {path.id}: {e}")
        except Exception as e:
            logger.error(f"Error loading existing jobs: {e}")
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
                    'interval',
                    seconds=path.check_interval_seconds,
                    id=job_id,
                    args=[path.id],
                    replace_existing=True
                )
                logger.info(f"Added scheduled job for path {path.id} ({path.name})")
        except Exception as e:
            logger.error(f"Error adding job for path {path.id}: {e}")
    
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
                'cron',
                hour=2,
                minute=0,
                id=job_id,
                replace_existing=True
            )
            logger.info("Added scheduled job for daily stats cleanup (runs at 2 AM)")
        except Exception as e:
            logger.error(f"Error adding stats cleanup job: {e}")


def scan_path_job_func(path_id: int):
    """
    Module-level function to scan a path.
    This is used by APScheduler to avoid serialization issues.
    Uses separate database session to avoid interfering with API requests.
    """
    db = SchedulerSessionLocal()
    try:
        path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
        if not path or not path.enabled:
            logger.debug(f"Path {path_id} not found or not enabled, skipping scan")
            return

        logger.info(f"Starting scan for path {path_id} ({path.name})")
        result = file_workflow_service.process_path(path, db)
        logger.info(f"Completed scan for path {path_id}: {result['files_moved']} files moved, {len(result['errors'])} errors")

    except Exception as e:
        logger.error(f"Error scanning path {path_id}: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning(f"Error closing scheduler database session: {e}")


# Global scheduler instance
scheduler_service = SchedulerService()

