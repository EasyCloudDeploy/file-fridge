"""Scheduler service for periodic file scans."""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app.models import MonitoredPath
from app.services.file_scanner import FileScanner
from app.services.file_mover import FileMover
from app.services.scan_processor import ScanProcessor

logger = logging.getLogger(__name__)


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
            job_defaults={'coalesce': True, 'max_instances': 1}
        )
        self.scan_processor = ScanProcessor()
    
    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")
            self._load_existing_jobs()
    
    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")
    
    def _load_existing_jobs(self):
        """Load existing monitored paths as scheduled jobs."""
        db = SessionLocal()
        try:
            paths = db.query(MonitoredPath).filter(MonitoredPath.enabled == True).all()
            for path in paths:
                self.add_path_job(path)
        finally:
            db.close()
    
    def add_path_job(self, path: MonitoredPath):
        """Add or update a scheduled job for a path."""
        job_id = f"scan_path_{path.id}"
        
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


def scan_path_job_func(path_id: int):
    """
    Module-level function to scan a path.
    This is used by APScheduler to avoid serialization issues.
    """
    db = SessionLocal()
    try:
        path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
        if not path or not path.enabled:
            return
        
        logger.info(f"Starting scan for path {path_id} ({path.name})")
        scan_processor = ScanProcessor()
        scan_processor.process_path(path, db)
    except Exception as e:
        logger.error(f"Error scanning path {path_id}: {str(e)}")
    finally:
        db.close()


# Global scheduler instance
scheduler_service = SchedulerService()

