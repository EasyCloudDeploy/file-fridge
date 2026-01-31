"""Storage routing service - selects optimal storage location for files."""

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileTransactionHistory,
    MonitoredPath,
)

logger = logging.getLogger(__name__)


@dataclass
class StorageCandidate:
    """Represents a storage location candidate with metrics."""

    location: ColdStorageLocation
    free_space_bytes: int
    file_count: int
    last_error_time: Optional[datetime]
    error_count: int
    score: float  # Higher is better


class StorageRoutingService:
    """Service for selecting optimal storage locations with load balancing."""

    MIN_FREE_SPACE_MB = 1024  # Minimum 1GB free space
    ERROR_GRACE_PERIOD_MINUTES = 15  # Don't use location with recent errors

    @staticmethod
    def select_storage_location(
        db: Session,
        monitored_path: MonitoredPath,
        file_size_bytes: int,
        file_extension: Optional[str] = None,
    ) -> Optional[ColdStorageLocation]:
        """
        Select the best storage location for a file.

        Uses a scoring algorithm that considers:
        1. Free space (higher is better)
        2. Current load (fewer files is better)
        3. Recent errors (avoid locations with recent failures)
        4. Storage affinity rules (if configured)

        Args:
            db: Database session
            monitored_path: The monitored path configuration
            file_size_bytes: Size of the file to store
            file_extension: File extension for affinity rules

        Returns:
            Selected ColdStorageLocation, or None if no suitable location found
        """
        if not monitored_path.storage_locations:
            logger.error(f"No storage locations configured for path {monitored_path.id}")
            return None

        candidates = StorageRoutingService._evaluate_candidates(db, monitored_path, file_size_bytes)

        if not candidates:
            logger.warning(
                f"No suitable storage locations for path {monitored_path.id} "
                f"(file size: {file_size_bytes} bytes)"
            )
            return None

        # Sort by score descending and pick the best
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]

        logger.info(
            f"Selected storage location: {best.location.name} (path={best.location.path}) "
            f"score={best.score:.2f}, free_space={best.free_space_bytes / 1024 / 1024 / 1024:.2f}GB"
        )

        return best.location

    @staticmethod
    def _evaluate_candidates(
        db: Session, monitored_path: MonitoredPath, file_size_bytes: int
    ) -> list[StorageCandidate]:
        """
        Evaluate all storage locations and score them.

        Args:
            db: Database session
            monitored_path: The monitored path configuration
            file_size_bytes: Size of the file to store

        Returns:
            List of candidates that meet minimum requirements
        """
        candidates = []
        now = datetime.now(tz=timezone.utc)
        error_cutoff = now - timedelta(minutes=StorageRoutingService.ERROR_GRACE_PERIOD_MINUTES)

        for location in monitored_path.storage_locations:
            try:
                path = Path(location.path)

                if not path.exists() or not path.is_dir():
                    logger.warning(f"Storage location not accessible: {location.path}")
                    continue

                # Get disk usage
                stat = shutil.disk_usage(path)
                free_space = stat.free
                total_space = stat.total

                # Check percentage-based thresholds
                free_percent = (free_space / total_space) * 100 if total_space > 0 else 0

                # Critical threshold check - reject location if below critical threshold
                if free_percent <= location.critical_threshold_percent:
                    logger.warning(
                        f"Location {location.name} below critical threshold: "
                        f"{free_percent:.2f}% free (threshold: {location.critical_threshold_percent}%)"
                    )
                    continue

                # Caution threshold check - log warning but still allow
                if free_percent <= location.caution_threshold_percent:
                    logger.info(
                        f"Location {location.name} below caution threshold: "
                        f"{free_percent:.2f}% free (threshold: {location.caution_threshold_percent}%)"
                    )

                # Minimum space check
                min_space = StorageRoutingService.MIN_FREE_SPACE_MB * 1024 * 1024
                if free_space < min_space:
                    logger.debug(
                        f"Location {location.name} insufficient space: {free_space} < {min_space}"
                    )
                    continue

                # Check if enough space for this file
                required_space = file_size_bytes + (1024 * 1024)  # +1MB buffer
                if free_space < required_space:
                    logger.debug(
                        f"Location {location.name} not enough space for file: {free_space} < {required_space}"
                    )
                    continue

                # Count files in this location
                file_count = (
                    db.query(FileInventory)
                    .filter(
                        FileInventory.path_id == monitored_path.id,
                        FileInventory.cold_storage_location_id == location.id,
                        FileInventory.storage_type == "cold",
                    )
                    .count()
                )

                # Get recent error count for this location
                recent_errors = (
                    db.query(FileTransactionHistory)
                    .filter(
                        FileTransactionHistory.new_storage_location_id == location.id,
                        not FileTransactionHistory.success,
                        FileTransactionHistory.created_at >= error_cutoff,
                    )
                    .count()
                )

                # Calculate score
                score = StorageRoutingService._calculate_score(
                    free_space, file_count, recent_errors
                )

                candidates.append(
                    StorageCandidate(
                        location=location,
                        free_space_bytes=free_space,
                        file_count=file_count,
                        last_error_time=None,  # Could be tracked if needed
                        error_count=recent_errors,
                        score=score,
                    )
                )

            except Exception as e:
                logger.error(
                    f"Error evaluating storage location {location.path}: {e}", exc_info=True
                )
                continue

        return candidates

    @staticmethod
    def _calculate_score(free_space: int, file_count: int, error_count: int) -> float:
        """
        Calculate a score for a storage location.

        Scoring factors:
        - Free space (normalized): 0-50 points
        - Load (inverse): 0-30 points
        - Error penalty: -10 points per recent error

        Args:
            free_space: Available space in bytes
            file_count: Number of files already stored
            error_count: Number of recent errors

        Returns:
            Score (higher is better)
        """
        # Free space score (logarithmic scaling)
        # 10GB = 50 points, 100GB = 100 points
        free_space_gb = free_space / (1024**3)
        space_score = min(50.0, 50.0 * (1 + (free_space_gb / 10.0) ** 0.5))

        # Load score (inverse - fewer files is better)
        # 0 files = 30 points, 10000 files = 0 points
        load_score = max(0.0, 30.0 * (1 - file_count / 10000.0))

        # Error penalty
        error_penalty = error_count * 10.0

        total_score = space_score + load_score - error_penalty
        return max(0.0, total_score)

    @staticmethod
    def has_sufficient_space(location: ColdStorageLocation, file_size_bytes: int) -> bool:
        """
        Quick check if a location has enough space for a file.

        Args:
            location: Storage location to check
            file_size_bytes: Size of the file to store

        Returns:
            True if location has sufficient space
        """
        try:
            path = Path(location.path)
            if not path.exists():
                return False

            stat = shutil.disk_usage(path)

            # Check percentage-based critical threshold
            free_percent = (stat.free / stat.total) * 100 if stat.total > 0 else 0
            if free_percent <= location.critical_threshold_percent:
                logger.debug(
                    f"Location {location.name} below critical threshold: "
                    f"{free_percent:.2f}% free (threshold: {location.critical_threshold_percent}%)"
                )
                return False

            min_space = StorageRoutingService.MIN_FREE_SPACE_MB * 1024 * 1024
            required_space = file_size_bytes + (1024 * 1024)  # +1MB buffer

            return stat.free >= max(min_space, required_space)

        except Exception:
            logger.exception(f"Error checking space for {location.path}")
            return False

    @staticmethod
    def get_location_health(
        db: Session, location: ColdStorageLocation, monitored_path_id: int
    ) -> dict:
        """
        Get health metrics for a storage location.

        Args:
            db: Database session
            location: Storage location to check
            monitored_path_id: Monitored path ID

        Returns:
            Dictionary with health metrics
        """
        try:
            path = Path(location.path)

            if not path.exists() or not path.is_dir():
                return {
                    "healthy": False,
                    "reason": "Location not accessible",
                    "free_space_bytes": 0,
                    "file_count": 0,
                    "recent_errors": 0,
                }

            stat = shutil.disk_usage(path)

            file_count = (
                db.query(FileInventory)
                .filter(
                    FileInventory.path_id == monitored_path_id,
                    FileInventory.cold_storage_location_id == location.id,
                    FileInventory.storage_type == "cold",
                )
                .count()
            )

            error_cutoff = datetime.now(tz=timezone.utc) - timedelta(
                minutes=StorageRoutingService.ERROR_GRACE_PERIOD_MINUTES
            )
            recent_errors = (
                db.query(FileTransactionHistory)
                .filter(
                    FileTransactionHistory.new_storage_location_id == location.id,
                    not FileTransactionHistory.success,
                    FileTransactionHistory.created_at >= error_cutoff,
                )
                .count()
            )

            healthy = (
                stat.free > StorageRoutingService.MIN_FREE_SPACE_MB * 1024 * 1024
                and recent_errors == 0
            )

            return {
                "healthy": healthy,
                "reason": (
                    None
                    if healthy
                    else f"{'Low space' if stat.free < StorageRoutingService.MIN_FREE_SPACE_MB * 1024 * 1024 else 'Recent errors'}"
                ),
                "free_space_bytes": stat.free,
                "free_space_gb": stat.free / (1024**3),
                "file_count": file_count,
                "recent_errors": recent_errors,
            }

        except Exception as e:
            logger.exception(f"Error getting health for {location.path}")
            return {
                "healthy": False,
                "reason": str(e),
                "free_space_bytes": 0,
                "file_count": 0,
                "recent_errors": 0,
            }


# Singleton instance
storage_routing_service = StorageRoutingService()
