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
    STORAGE_PERMISSION_ERROR = (
        "STORAGE_PERMISSION_ERROR"  # Read/write permission denied on storage path
    )


# Event data models (for type safety and validation)


class StorageLocationStats(BaseModel):
    """Disk usage snapshot for a single storage location."""

    name: str
    free_bytes: int
    total_bytes: int
    free_percent: float


class ScanCompletedData(BaseModel):
    """Data for SCAN_COMPLETED event."""

    path_id: int
    path_name: str
    files_scanned: int = 0
    files_moved: int
    files_skipped: int = 0
    bytes_moved: int
    scan_duration_seconds: float
    errors: int = 0
    cold_storages_updated: list[str] = []
    hot_storage: Optional[StorageLocationStats] = None
    cold_storages: list[StorageLocationStats] = []


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


class StoragePermissionErrorData(BaseModel):
    """Data for STORAGE_PERMISSION_ERROR event."""

    storage_type: str  # "hot" or "cold"
    location_name: str
    location_path: str
    missing_permissions: list[str]  # e.g. ["read", "write"]


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
    StoragePermissionErrorData,
]
