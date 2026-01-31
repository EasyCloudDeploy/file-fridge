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
    DiskSpaceCautionData,
    DiskSpaceCriticalData,
    NotificationEventType,
    ScanCompletedData,
    ScanErrorData,
)
from app.services.notification_service import notification_service
from app.services.remote_transfer_service import remote_transfer_service
from app.services.stats_cleanup import cleanup_old_stats_job_func
from app.utils.remote_auth import remote_auth

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
                self._add_disk_space_monitoring_job()
                self._add_nonce_cleanup_job()
                self._add_remote_code_rotation_job()
                self._add_remote_transfer_job()
            except Exception:
                logger.exception("Error starting scheduler")
                # Try to clean up
                try:
                    if self.scheduler.running:
                        self.scheduler.shutdown(wait=False)
                except Exception:
                    pass
                raise

    def stop(self):
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            try:
                # Shutdown gracefully, waiting for running jobs to complete
                self.scheduler.shutdown(wait=True)
                logger.info("Scheduler stopped gracefully")
            except Exception:
                logger.warning("Error during scheduler shutdown")
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
                except Exception:
                    logger.exception(f"Error loading job for path {path.id}")
        except Exception:
            logger.exception("Error loading existing jobs")
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
        except Exception:
            logger.exception(f"Error adding job for path {path.id}")

    def remove_path_job(self, path_id: int):
        """Remove scheduled job for a path."""
        job_id = f"scan_path_{path_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed scheduled job for path {path_id}")

    def trigger_scan(self, path_id: int):
        """Manually trigger a scan for a path."""
        scan_path_job_func(path_id)

    def trigger_encryption_job(self, location_id: int):
        """Trigger background job to encrypt all files in a location."""
        self.scheduler.add_job(
            encrypt_location_job_func,
            id=f"encrypt_location_{location_id}",
            args=[location_id],
            replace_existing=True,
        )

    def trigger_decryption_job(self, location_id: int):
        """Trigger background job to decrypt all files in a location."""
        self.scheduler.add_job(
            decrypt_location_job_func,
            id=f"decrypt_location_{location_id}",
            args=[location_id],
            replace_existing=True,
        )

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
        except Exception:
            logger.exception("Error adding stats cleanup job")

    def _add_remote_transfer_job(self):
        """Add scheduled job for processing remote transfers."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping remote transfer job addition")
            return

        job_id = "remote_transfer_processing"
        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Schedule to run every minute
            self.scheduler.add_job(
                process_remote_transfers_job_func,
                "interval",
                minutes=1,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for processing remote transfers (runs every minute)")
        except Exception:
            logger.exception("Error adding remote transfer job")

    def _add_remote_code_rotation_job(self):
        """Add scheduled job for rotating remote connection code hourly."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping remote code rotation job addition")
            return

        job_id = "remote_code_rotation"
        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Schedule to run every hour
            self.scheduler.add_job(
                rotate_remote_code_job_func,
                "interval",
                hours=1,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for hourly remote code rotation")
        except Exception:
            logger.exception("Error adding remote code rotation job")

    def _add_nonce_cleanup_job(self):
        """Add scheduled job for cleaning up old request nonces (runs every hour)."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping nonce cleanup job addition")
            return

        job_id = "nonce_cleanup"
        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Schedule to run every hour
            self.scheduler.add_job(
                cleanup_old_nonces_job_func,
                "interval",
                hours=1,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for nonce cleanup (runs every hour)")
        except Exception as e:
            logger.exception(f"Error adding nonce cleanup job: {e}")

    def _add_disk_space_monitoring_job(self):
        """Add scheduled job for disk space monitoring (runs every 10 minutes)."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping disk space monitoring job addition")
            return

        job_id = "disk_space_monitoring"
        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Schedule to run every 10 minutes
            self.scheduler.add_job(
                disk_space_monitoring_job_func,
                "interval",
                minutes=10,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for disk space monitoring (runs every 10 minutes)")
        except Exception as e:
            logger.exception(f"Error adding disk space monitoring job: {e}")


def _check_and_notify_disk_space(location, db: Session):
    """
    Check disk space for a cold storage location and send notifications if low.

    Args:
        location: ColdStorageLocation instance to check
        db: Database session

    Returns:
        Tuple of (result_level, free_percent) where result_level is "critical", "caution", or None
    """
    total, _used, free = shutil.disk_usage(location.path)
    free_percent = (free / total) * 100

    # Check critical threshold first (more severe)
    if free_percent <= location.critical_threshold_percent:
        payload = DiskSpaceCriticalData(
            location_id=location.id,
            location_name=location.name,
            location_path=location.path,
            free_percent=round(free_percent, 2),
            threshold_percent=location.critical_threshold_percent,
            free_bytes=free,
            total_bytes=total,
        )
        try:
            notification_service.dispatch_event_sync(
                db=db,
                event_type=NotificationEventType.DISK_SPACE_CRITICAL,
                event_data=payload,
            )
        except Exception as e:
            logger.error(f"Failed to dispatch DISK_SPACE_CRITICAL notification: {e}")
        return ("critical", free_percent)

    # Check caution threshold (only if not already critical)
    if free_percent <= location.caution_threshold_percent:
        payload = DiskSpaceCautionData(
            location_id=location.id,
            location_name=location.name,
            location_path=location.path,
            free_percent=round(free_percent, 2),
            threshold_percent=location.caution_threshold_percent,
            free_bytes=free,
            total_bytes=total,
        )
        try:
            notification_service.dispatch_event_sync(
                db=db,
                event_type=NotificationEventType.DISK_SPACE_CAUTION,
                event_data=payload,
            )
        except Exception as e:
            logger.error(f"Failed to dispatch DISK_SPACE_CAUTION notification: {e}")
        return ("caution", free_percent)

    return (None, free_percent)


def check_disk_space_and_notify(path: MonitoredPath, db: Session):
    """Check disk space for all cold storage locations and send notifications if low."""
    for location in path.storage_locations:
        try:
            _check_and_notify_disk_space(location, db)
        except FileNotFoundError:
            logger.warning(
                f"Could not check disk space for {location.name}: path not found at {location.path}"
            )
        except Exception:
            logger.exception(f"Error checking disk space for {location.name}")


def process_remote_transfers_job_func():
    """Job function to process pending remote transfers."""
    import asyncio

    try:
        # Create a new event loop for this thread if needed
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(remote_transfer_service.process_pending_transfers())
    except Exception:
        logger.exception("Error in remote transfer job")


def rotate_remote_code_job_func():
    """Job function to rotate the remote connection code."""
    remote_auth.rotate_code()
    logger.info("Rotated remote connection code")


def disk_space_monitoring_job_func():
    """Background job to monitor disk space on all cold storage locations (runs every 10 minutes)."""
    from app.models import ColdStorageLocation

    db = SchedulerSessionLocal()
    try:
        locations = db.query(ColdStorageLocation).all()
        logger.info(f"Checking disk space for {len(locations)} cold storage locations")

        for location in locations:
            try:
                result_level, free_percent = _check_and_notify_disk_space(location, db)
                if result_level == "critical":
                    logger.warning(
                        f"CRITICAL: Disk space on {location.name} at {free_percent:.1f}% free (threshold: {location.critical_threshold_percent}%)"
                    )
                elif result_level == "caution":
                    logger.warning(
                        f"CAUTION: Disk space on {location.name} at {free_percent:.1f}% free (threshold: {location.caution_threshold_percent}%)"
                    )
            except FileNotFoundError:
                logger.warning(
                    f"Could not check disk space for {location.name}: path not found at {location.path}"
                )
            except Exception as e:
                logger.exception(f"Error checking disk space for {location.name}: {e}")

    finally:
        db.close()


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
                error_payload = ScanErrorData(
                    path_id=path_id,
                    path_name=path.name,
                    error_message=error_msg,
                    error_details=None,
                )
                try:
                    notification_service.dispatch_event_sync(
                        db=db,
                        event_type=NotificationEventType.SCAN_ERROR,
                        event_data=error_payload,
                    )
                except Exception as e:
                    logger.error(f"Failed to dispatch SCAN_ERROR notification: {e}")

        # Send scan completed notification
        success_payload = ScanCompletedData(
            path_id=path_id,
            path_name=path.name,
            files_moved=result.get("files_moved", 0),
            bytes_saved=result.get("bytes_saved", 0),
            scan_duration_seconds=round(duration, 2),
            errors=len(result.get("errors", [])),
        )
        try:
            notification_service.dispatch_event_sync(
                db=db,
                event_type=NotificationEventType.SCAN_COMPLETED,
                event_data=success_payload,
            )
        except Exception as e:
            logger.error(f"Failed to dispatch SCAN_COMPLETED notification: {e}")

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
        error_payload = ScanErrorData(
            path_id=path_id,
            path_name=path.name if path else f"ID {path_id}",
            error_message=f"A fatal error occurred during scan: {e!s}",
            error_details=tb_str,
        )
        try:
            notification_service.dispatch_event_sync(
                db=db,
                event_type=NotificationEventType.SCAN_ERROR,
                event_data=error_payload,
            )
        except Exception as notify_error:
            logger.error(
                f"Failed to dispatch SCAN_ERROR notification for fatal scan error: {notify_error}"
            )
    finally:
        try:
            db.close()
        except Exception:
            logger.warning("Error closing scheduler database session")


def encrypt_location_job_func(location_id: int):
    """Job to encrypt all files in a storage location."""
    from pathlib import Path

    from app.models import ColdStorageLocation, EncryptionStatus, FileInventory, StorageType
    from app.services.encryption_service import file_encryption_service

    db = SchedulerSessionLocal()
    try:
        location = (
            db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
        )
        if not location:
            logger.error(f"Location {location_id} not found for encryption job")
            return

        logger.info(f"Starting bulk encryption for location {location.name}")

        # Get all unencrypted files in this location
        files = (
            db.query(FileInventory)
            .filter(
                FileInventory.cold_storage_location_id == location_id,
                FileInventory.storage_type == StorageType.COLD,
                not FileInventory.is_encrypted,
            )
            .all()
        )

        total = len(files)
        logger.info(f"Found {total} files to encrypt")

        success_count = 0

        for file in files:
            try:
                source_path = Path(file.file_path)
                if not source_path.exists():
                    logger.warning(f"File missing during encryption: {source_path}")
                    continue

                target_path = source_path.with_suffix(source_path.suffix + ".ffenc")

                # Encrypt
                file_encryption_service.encrypt_file(db, source_path, target_path)

                # Update DB (but don't commit until file operations are safe)
                file.file_path = str(target_path)
                file.is_encrypted = True

                # Delete original file
                try:
                    source_path.unlink()
                    # Only commit if deletion succeeded (or file was gone)
                    db.commit()
                    success_count += 1
                except Exception:
                    # Failed to delete source, rollback DB changes to match filesystem state
                    # (where source still exists, possibly alongside target)
                    db.rollback()
                    # Clean up target if we can't switch over
                    if target_path.exists():
                        target_path.unlink()
                    raise

            except Exception:
                db.rollback()
                logger.exception(f"Failed to encrypt file {file.id}")
                # Continue with other files

        # Update location status
        location.encryption_status = EncryptionStatus.ENCRYPTED
        db.commit()
        logger.info(
            f"Completed encryption for location {location.name}. Encrypted {success_count}/{total} files."
        )

    except Exception:
        logger.exception(f"Error in encryption job for location {location_id}")
    finally:
        db.close()


def decrypt_location_job_func(location_id: int):
    """Job to decrypt all files in a storage location."""
    from pathlib import Path

    from app.models import ColdStorageLocation, EncryptionStatus, FileInventory, StorageType
    from app.services.encryption_service import file_encryption_service

    db = SchedulerSessionLocal()
    try:
        location = (
            db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
        )
        if not location:
            logger.error(f"Location {location_id} not found for decryption job")
            return

        logger.info(f"Starting bulk decryption for location {location.name}")

        # Get all encrypted files in this location
        files = (
            db.query(FileInventory)
            .filter(
                FileInventory.cold_storage_location_id == location_id,
                FileInventory.storage_type == StorageType.COLD,
                FileInventory.is_encrypted,
            )
            .all()
        )

        total = len(files)
        logger.info(f"Found {total} files to decrypt")

        success_count = 0

        for file in files:
            try:
                source_path = Path(file.file_path)
                if not source_path.exists():
                    logger.warning(f"File missing during decryption: {source_path}")
                    continue

                # Remove .ffenc suffix if present
                if source_path.suffix == ".ffenc":
                    target_path = source_path.with_suffix("")
                else:
                    # Fallback if no suffix (shouldn't happen with our naming convention but good to handle)
                    target_path = source_path.with_name(source_path.name + ".decrypted")

                # Decrypt
                file_encryption_service.decrypt_file(db, source_path, target_path)

                # Update DB
                file.file_path = str(target_path)
                file.is_encrypted = False

                try:
                    # Delete encrypted original
                    source_path.unlink()
                    # Commit only after successful filesystem update
                    db.commit()
                    success_count += 1
                except Exception:
                    db.rollback()
                    # Clean up target
                    if target_path.exists():
                        target_path.unlink()
                    raise

            except Exception:
                db.rollback()
                logger.exception(f"Failed to decrypt file {file.id}")

        # Update location status
        location.encryption_status = EncryptionStatus.NONE
        db.commit()
        logger.info(
            f"Completed decryption for location {location.name}. Decrypted {success_count}/{total} files."
        )

    except Exception:
        logger.exception(f"Error in decryption job for location {location_id}")
    finally:
        db.close()


def cleanup_old_nonces_job_func():
    """Job function to clean up old request nonces (runs every hour)."""
    import time

    from app.config import settings
    from app.models import RequestNonce

    db = SchedulerSessionLocal()
    try:
        # Clean up nonces older than signature_timestamp_tolerance + buffer (6 minutes total)
        cutoff_time = int(time.time()) - (settings.signature_timestamp_tolerance + 60)
        deleted = db.query(RequestNonce).filter(RequestNonce.timestamp < cutoff_time).delete()
        db.commit()
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old request nonces")
    except Exception as e:
        logger.exception("Error cleaning up old nonces", exc_info=e)
        db.rollback()
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning("Error closing scheduler database session in nonce cleanup", exc_info=e)


# Global scheduler instance
scheduler_service = SchedulerService()
