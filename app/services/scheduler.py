import logging
import os
import shutil
import time
import traceback

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine
from app.models import MonitoredPath
from app.services.cold_storage_backends import get_backend
from app.services.file_workflow_service import file_workflow_service
from app.services.notification_events import (
    DiskSpaceCautionData,
    DiskSpaceCriticalData,
    NotificationEventType,
    ScanCompletedData,
    ScanErrorData,
    StoragePermissionErrorData,
)
from app.services.notification_service import notification_service
from app.services.p2p_service import p2p_service
from app.services.stats_cleanup import cleanup_old_stats_job_func
from app.utils.local_drive_identity import update_local_drive_identity_fields

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
                self._add_storage_permissions_job()
                self._add_nonce_cleanup_job()
                self._add_fftmp_cleanup_job()
                self._add_transfer_job_cleanup_job()
                self._add_p2p_manifest_sync_job()
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
                # Do not wait for long-running transfer or storage jobs during container shutdown.
                self.scheduler.shutdown(wait=False)
                logger.info("Scheduler stopped")
            except Exception:
                logger.warning("Error during scheduler shutdown")

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

    def trigger_scan(self, path_id: int) -> None:
        """Manually trigger a scan for a path."""
        self.scheduler.add_job(
            scan_path_job_func,
            id=f"manual_scan_path_{path_id}",
            args=[path_id],
            replace_existing=True,
            misfire_grace_time=None,
        )

    def trigger_encryption_job(self, location_id: int) -> None:
        """Trigger background job to encrypt all files in a location."""
        self.scheduler.add_job(
            encrypt_location_job_func,
            id=f"encrypt_location_{location_id}",
            args=[location_id],
            replace_existing=True,
            misfire_grace_time=None,
        )

    def trigger_decryption_job(self, location_id: int) -> None:
        """Trigger background job to decrypt all files in a location."""
        self.scheduler.add_job(
            decrypt_location_job_func,
            id=f"decrypt_location_{location_id}",
            args=[location_id],
            replace_existing=True,
            misfire_grace_time=None,
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

    def _add_nonce_cleanup_job(self):
        """Add scheduled job for cleaning up old request nonces (runs every 10 minutes)."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping nonce cleanup job addition")
            return

        job_id = "nonce_cleanup"
        try:
            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Schedule to run every 10 minutes as per PLAN.md
            self.scheduler.add_job(
                cleanup_old_nonces_job_func,
                "interval",
                minutes=10,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for nonce cleanup (runs every 10 minutes)")
        except Exception:
            logger.exception("Error adding nonce cleanup job")

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

    def _add_storage_permissions_job(self) -> None:
        """Add scheduled job for storage permissions checking (runs every hour)."""
        if not self.scheduler.running:
            logger.warning("Scheduler not running, skipping storage permissions job addition")
            return

        job_id = "storage_permissions_check"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            self.scheduler.add_job(
                check_storage_permissions_job_func,
                "interval",
                hours=1,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for storage permissions check (runs every hour)")
        except Exception as e:
            logger.exception(f"Error adding storage permissions job: {e}")

    def _add_fftmp_cleanup_job(self):
        """Add scheduled job for cleaning up orphaned .fftmp files (runs daily at 3 AM)."""
        if not self.scheduler.running:
            return

        job_id = "fftmp_cleanup"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            self.scheduler.add_job(
                cleanup_orphaned_fftmp_job_func,
                "cron",
                hour=3,
                minute=0,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for daily .fftmp cleanup (runs at 3 AM)")
        except Exception:
            logger.exception("Error adding fftmp cleanup job")

    def _add_transfer_job_cleanup_job(self):
        """Add scheduled job for cleaning up old transfer job records (runs weekly on Sunday at 4 AM)."""
        if not self.scheduler.running:
            return

        job_id = "transfer_job_cleanup"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            self.scheduler.add_job(
                cleanup_old_transfer_jobs_job_func,
                "cron",
                day_of_week="sun",
                hour=4,
                minute=0,
                id=job_id,
                replace_existing=True,
            )
            logger.info(
                "Added scheduled job for weekly transfer job cleanup (runs Sundays at 4 AM)"
            )
        except Exception:
            logger.exception("Error adding transfer job cleanup job")

    def _add_p2p_manifest_sync_job(self):
        """Add scheduled job for syncing remote manifests from configured peers."""
        if not self.scheduler.running:
            return

        job_id = "p2p_manifest_sync"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            self.scheduler.add_job(
                sync_p2p_manifests_job_func,
                "interval",
                minutes=1,
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added scheduled job for P2P manifest sync (runs every minute)")
        except Exception:
            logger.exception("Error adding P2P manifest sync job")


def _check_and_notify_disk_space(location, db: Session):
    """
    Check disk space for a cold storage location and send notifications if low.

    Args:
        location: ColdStorageLocation instance to check
        db: Database session

    Returns:
        Tuple of (result_level, free_percent) where result_level is "critical", "caution", or None
    """
    backend = get_backend(location)
    if not backend.capabilities().supports_local_path_stats:
        return (None, None)

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


def sync_p2p_manifests_job_func():
    """Fetch manifests from all configured peers and refresh the local remote-file cache."""
    db = SchedulerSessionLocal()
    try:
        synced = p2p_service.sync_all_peer_manifests(db)
        logger.debug("P2P manifest sync processed %s peer(s)", synced)
    except Exception:
        logger.exception("Error in P2P manifest sync job")
    finally:
        db.close()


def _check_path_permissions(path: str) -> list[str]:
    """Return list of missing permissions ('read', 'write') for the given path."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path does not exist: {path}")

    missing = []
    if not os.access(path, os.R_OK):
        missing.append("read")
    if not os.access(path, os.W_OK):
        missing.append("write")
    return missing


def _local_disk_space_level(location) -> tuple[str | None, float | None]:
    """Return the local disk-space alert level for a cold storage location."""
    backend = get_backend(location)
    if not backend.capabilities().supports_local_path_stats:
        return None, None

    total, _used, free = shutil.disk_usage(location.path)
    if total <= 0:
        return None, None

    free_percent = (free / total) * 100
    if free_percent <= location.critical_threshold_percent:
        return "critical", free_percent
    if free_percent <= location.caution_threshold_percent:
        return "caution", free_percent
    return None, free_percent


def check_storage_permissions_job_func() -> None:
    """Background job to verify read/write access on all hot and cold storage paths (runs every hour)."""
    from app.models import ColdStorageLocation, MonitoredPath

    db = SchedulerSessionLocal()
    try:
        # Check cold storage locations
        locations = db.query(ColdStorageLocation).all()
        for location in locations:
            try:
                backend = get_backend(location)
                update_local_drive_identity_fields(location)
                backend_type = (
                    location.backend_type.value
                    if hasattr(location.backend_type, "value")
                    else str(location.backend_type)
                )
                expected_offline = (
                    backend_type == "local"
                    and location.allow_offline
                    and location.local_drive_is_removable
                    and location.local_drive_is_connected is False
                )
                if expected_offline:
                    if location.permissions_error is not None:
                        logger.info(
                            "Cold storage '%s' is offline by design (allow_offline); suppressing permission error",
                            location.name,
                        )
                    location.permissions_error = None
                    continue
                if backend.capabilities().supports_local_path_stats:
                    missing = _check_path_permissions(location.path)
                    if missing:
                        disk_space_level, free_percent = _local_disk_space_level(location)
                        if disk_space_level == "critical" and missing == ["write"]:
                            if location.permissions_error is not None:
                                logger.info(
                                    "Clearing stale permission error on cold storage '%s'; "
                                    "path is writable enough to inspect but disk space is critical",
                                    location.name,
                                )
                            location.permissions_error = None
                            logger.warning(
                                "Cold storage '%s' has critical low disk space (%.1f%% free; "
                                "threshold: %s%%); not reporting this as a write permission error",
                                location.name,
                                free_percent,
                                location.critical_threshold_percent,
                            )
                            continue

                        error = f"Missing {' and '.join(missing)} permission on cold storage path: {location.path}"
                        if location.permissions_error != error:
                            location.permissions_error = error
                            logger.warning(
                                f"Permission issue on cold storage '{location.name}': {error}"
                            )
                            try:
                                notification_service.dispatch_event_sync(
                                    db=db,
                                    event_type=NotificationEventType.STORAGE_PERMISSION_ERROR,
                                    event_data=StoragePermissionErrorData(
                                        storage_type="cold",
                                        location_name=location.name,
                                        location_path=location.path,
                                        missing_permissions=missing,
                                    ),
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to dispatch STORAGE_PERMISSION_ERROR notification: {e}"
                                )
                    else:
                        if location.permissions_error is not None:
                            logger.info(f"Permissions restored on cold storage '{location.name}'")
                        location.permissions_error = None
                else:
                    is_valid, validation_error = backend.validate_location(location)
                    if not is_valid:
                        location.permissions_error = validation_error or "Backend validation failed"
                    else:
                        if location.permissions_error is not None:
                            logger.info(f"Permissions restored on cold storage '{location.name}'")
                        location.permissions_error = None
            except FileNotFoundError:
                backend_type = (
                    location.backend_type.value
                    if hasattr(location.backend_type, "value")
                    else str(location.backend_type)
                )
                expected_offline = (
                    backend_type == "local"
                    and location.allow_offline
                    and location.local_drive_is_removable
                    and location.local_drive_is_connected is False
                )
                if expected_offline:
                    location.permissions_error = None
                    logger.info(
                        "Cold storage '%s' path is missing while offline is allowed; skipping error",
                        location.name,
                    )
                else:
                    error = f"Path not found: {location.path}"
                    location.permissions_error = error
                    logger.warning(
                        f"Cold storage path not found for '{location.name}': {location.path}"
                    )
            except Exception:
                logger.exception(f"Error checking permissions for cold storage '{location.name}'")

        # Check hot storage (monitored paths)
        paths = db.query(MonitoredPath).all()
        for path in paths:
            try:
                missing = _check_path_permissions(path.source_path)
                if missing:
                    error = f"Missing {' and '.join(missing)} permission on hot storage path: {path.source_path}"
                    if path.permissions_error != error:
                        path.permissions_error = error
                        logger.warning(f"Permission issue on hot storage '{path.name}': {error}")
                        try:
                            notification_service.dispatch_event_sync(
                                db=db,
                                event_type=NotificationEventType.STORAGE_PERMISSION_ERROR,
                                event_data=StoragePermissionErrorData(
                                    storage_type="hot",
                                    location_name=path.name,
                                    location_path=path.source_path,
                                    missing_permissions=missing,
                                ),
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to dispatch STORAGE_PERMISSION_ERROR notification: {e}"
                            )
                else:
                    if path.permissions_error is not None:
                        logger.info(f"Permissions restored on hot storage '{path.name}'")
                    path.permissions_error = None
            except FileNotFoundError:
                path.permissions_error = f"Path not found: {path.source_path}"
                logger.warning(f"Hot storage path not found for '{path.name}': {path.source_path}")
            except Exception:
                logger.exception(f"Error checking permissions for hot storage '{path.name}'")

        db.commit()
        logger.info(
            f"Storage permissions check complete: {len(locations)} cold locations, {len(paths)} hot paths"
        )
    finally:
        db.close()


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
                elif free_percent is None:
                    logger.debug(
                        "Skipping local disk-space check for non-local cold storage '%s' (%s)",
                        location.name,
                        location.path,
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
                ~FileInventory.is_encrypted,
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
    """Job function to clean up old request nonces (runs every 10 minutes)."""
    import time

    from app.models import RequestNonce

    db = SchedulerSessionLocal()
    try:
        # Clean up nonces older than 600 seconds (2x signature tolerance) as per PLAN.md
        cutoff_time = int(time.time()) - 600
        deleted = db.query(RequestNonce).filter(RequestNonce.timestamp < cutoff_time).delete()
        db.commit()
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old request nonces")
    except Exception:
        logger.exception("Error cleaning up old nonces")
        db.rollback()
    finally:
        try:
            db.close()
        except Exception as e:
            logger.warning("Error closing scheduler database session in nonce cleanup", exc_info=e)


def cleanup_orphaned_fftmp_job_func():
    """Daily job to clean up orphaned .fftmp files older than 7 days."""
    from datetime import datetime, timedelta
    from pathlib import Path

    from app.models import ColdStorageLocation, MonitoredPath

    db = SchedulerSessionLocal()
    try:
        # Get all monitored paths and storage locations
        monitored_paths = db.query(MonitoredPath).all()
        storage_locations = db.query(ColdStorageLocation).all()

        all_paths = set()
        for mp in monitored_paths:
            all_paths.add(mp.source_path)
        for sl in storage_locations:
            all_paths.add(sl.path)

        now = datetime.now()
        cleanup_threshold = now - timedelta(days=7)
        warning_threshold = now - timedelta(days=1)

        deleted_count = 0
        warning_count = 0

        for path_str in all_paths:
            base_path = Path(path_str)
            if not base_path.exists() or not base_path.is_dir():
                continue

            # Scan for .fftmp files
            for fftmp_file in base_path.glob("**/*.fftmp"):
                try:
                    mtime = datetime.fromtimestamp(fftmp_file.stat().st_mtime)
                    if mtime < cleanup_threshold:
                        fftmp_file.unlink()
                        deleted_count += 1
                        logger.info(f"Deleted orphaned .fftmp file: {fftmp_file}")
                    elif mtime < warning_threshold:
                        warning_count += 1
                        logger.warning(f"Found old .fftmp file (stuck transfer?): {fftmp_file}")
                except Exception as e:
                    logger.error(f"Error processing .fftmp file {fftmp_file}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} orphaned .fftmp files")

    except Exception:
        logger.exception("Error in orphaned .fftmp cleanup job")
    finally:
        db.close()


def cleanup_old_transfer_jobs_job_func():
    """Weekly job to clean up terminal transfer jobs older than 90 days."""
    from datetime import datetime, timedelta, timezone

    from app.models import RemoteTransferJob, TransferStatus

    db = SchedulerSessionLocal()
    try:
        # Retention period 90 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)

        terminal_statuses = [
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
        ]

        deleted = (
            db.query(RemoteTransferJob)
            .filter(
                RemoteTransferJob.status.in_(terminal_statuses),
                RemoteTransferJob.updated_at < cutoff,
            )
            .delete(synchronize_session=False)
        )

        db.commit()
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old transfer job records")
    except Exception:
        logger.exception("Error cleaning up old transfer jobs")
        db.rollback()
    finally:
        db.close()


# Global scheduler instance
scheduler_service = SchedulerService()
