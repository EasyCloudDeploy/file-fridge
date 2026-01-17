"""Defines the structured events for the notification system."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class NotificationEvent(str, Enum):
    """Enum for the different types of notification events."""

    SYNC_SUCCESS = "sync_success"
    SYNC_ERROR = "sync_error"
    LOW_DISK_SPACE = "low_disk_space"


class SyncSuccessData(BaseModel):
    """Data payload for a successful sync event."""

    path_name: str
    files_scanned: int
    files_moved_to_cold: int
    files_thawed_from_cold: int
    duration_seconds: float


class SyncErrorData(BaseModel):
    """Data payload for a failed sync event."""

    path_name: str
    error_message: str
    traceback: Optional[str] = None


class LowDiskSpaceData(BaseModel):
    """Data payload for a low disk space warning."""

    storage_name: str
    storage_path: str
    free_space_gb: float
    total_space_gb: float
    threshold_percent: float
