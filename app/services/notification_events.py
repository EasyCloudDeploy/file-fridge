"""Defines the structured events for the notification system."""

from enum import Enum
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel


class NotificationEventType(str, Enum):
    """All supported notification event types."""

    # Scan events (renamed from SYNC_*)
    SCAN_COMPLETED = "SCAN_COMPLETED"  # Scan finishes successfully
    SCAN_ERROR = "SCAN_ERROR"  # Scan fails or encounters exceptions

    # Path lifecycle events
    PATH_CREATED = "PATH_CREATED"  # New MonitoredPath added
    PATH_UPDATED = "PATH_UPDATED"  # Path configuration modified
    PATH_DELETED = "PATH_DELETED"  # Path removed

    # Storage health events
    DISK_SPACE_CAUTION = "DISK_SPACE_CAUTION"  # Free space drops below caution threshold
    DISK_SPACE_CRITICAL = "DISK_SPACE_CRITICAL"  # Free space drops below critical threshold


# Event data models (for type safety and validation)


class ScanCompletedData(BaseModel):
    """Data for SCAN_COMPLETED event."""

    path_id: int
    path_name: str
    files_moved: int
    bytes_saved: int
    scan_duration_seconds: float
    errors: int = 0


class ScanErrorData(BaseModel):
    """Data for SCAN_ERROR event."""

    path_id: int
    path_name: str
    error_message: str
    error_details: Optional[str] = None


class PathCreatedData(BaseModel):
    """Data for PATH_CREATED event."""

    path_id: int
    path_name: str
    source_path: str
    operation_type: str
    created_by: Optional[str] = None


class PathUpdatedData(BaseModel):
    """Data for PATH_UPDATED event."""

    path_id: int
    path_name: str
    changes: Dict[str, Any]  # Field name → new value
    updated_by: Optional[str] = None


class PathDeletedData(BaseModel):
    """Data for PATH_DELETED event."""

    path_id: int
    path_name: str
    source_path: str
    deleted_by: Optional[str] = None


class DiskSpaceCautionData(BaseModel):
    """Data for DISK_SPACE_CAUTION event."""

    location_id: int
    location_name: str
    location_path: str
    free_percent: float
    threshold_percent: int
    free_bytes: int
    total_bytes: int


class DiskSpaceCriticalData(BaseModel):
    """Data for DISK_SPACE_CRITICAL event."""

    location_id: int
    location_name: str
    location_path: str
    free_percent: float
    threshold_percent: int
    free_bytes: int
    total_bytes: int


# Type alias for all event data
EventData = Union[
    ScanCompletedData,
    ScanErrorData,
    PathCreatedData,
    PathUpdatedData,
    PathDeletedData,
    DiskSpaceCautionData,
    DiskSpaceCriticalData,
]
