"""Pydantic schemas for API validation."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, TypeAdapter, validator

from app.models import (
    CriterionType,
    DispatchStatus,
    FileStatus,
    NotificationLevel,
    NotifierType,
    OperationType,
    Operator,
    ScanStatus,
    StorageType,
    TagRuleCriterionType,
    TransferStatus,
)


class CriteriaBase(BaseModel):
    """Base criteria schema."""

    criterion_type: CriterionType
    operator: Operator
    value: str
    enabled: bool = True


class CriteriaCreate(CriteriaBase):
    """Schema for creating criteria."""


class CriteriaUpdate(BaseModel):
    """Schema for updating criteria."""

    criterion_type: Optional[CriterionType] = None
    operator: Optional[Operator] = None
    value: Optional[str] = None
    enabled: Optional[bool] = None


class Criteria(CriteriaBase):
    """Schema for criteria response."""

    id: int
    path_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ColdStorageLocationBase(BaseModel):
    """Base cold storage location schema."""

    name: str = Field(..., min_length=1, max_length=255)
    path: str = Field(..., min_length=1)
    caution_threshold_percent: int = Field(
        20, ge=0, le=100, description="Warn at this % free space"
    )
    critical_threshold_percent: int = Field(
        10, ge=0, le=100, description="Critical at this % free space"
    )

    @validator("critical_threshold_percent")
    @classmethod
    def validate_critical_less_than_caution(cls, v, values):
        """Ensure critical threshold < caution threshold."""
        if "caution_threshold_percent" in values:
            if v >= values["caution_threshold_percent"]:
                raise ValueError(
                    "critical_threshold_percent must be less than caution_threshold_percent"
                )
        return v


class ColdStorageLocationCreate(ColdStorageLocationBase):
    """Schema for creating cold storage location."""


class ColdStorageLocationUpdate(BaseModel):
    """Schema for updating cold storage location."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    path: Optional[str] = Field(None, min_length=1)
    caution_threshold_percent: Optional[int] = Field(None, ge=0, le=100)
    critical_threshold_percent: Optional[int] = Field(None, ge=0, le=100)


class ColdStorageLocation(ColdStorageLocationBase):
    """Schema for cold storage location response."""

    id: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ColdStorageLocationWithStats(ColdStorageLocation):
    """Schema for cold storage location with path count."""

    path_count: int


class MonitoredPathBase(BaseModel):
    """Base monitored path schema."""

    name: str = Field(..., min_length=1, max_length=255)
    source_path: str = Field(..., min_length=1)
    operation_type: OperationType = OperationType.MOVE
    check_interval_seconds: int = Field(..., ge=60)  # Minimum 1 minute
    enabled: bool = True
    prevent_indexing: bool = (
        True  # Create .noindex file to prevent macOS Spotlight from corrupting timestamps
    )
    error_message: Optional[str] = None  # Error state message
    last_scan_at: Optional[datetime] = None  # When the last scan finished
    last_scan_status: Optional[ScanStatus] = None  # Status of the last scan


class MonitoredPathCreate(MonitoredPathBase):
    """Schema for creating monitored path."""

    storage_location_ids: List[int] = Field(
        ..., min_items=1, description="List of cold storage location IDs (at least one required)"
    )


class MonitoredPathUpdate(BaseModel):
    """Schema for updating monitored path."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    source_path: Optional[str] = Field(None, min_length=1)
    operation_type: Optional[OperationType] = None
    check_interval_seconds: Optional[int] = Field(None, ge=60)
    enabled: Optional[bool] = None
    prevent_indexing: Optional[bool] = None
    storage_location_ids: Optional[List[int]] = Field(
        None, min_items=1, description="List of cold storage location IDs"
    )


class MonitoredPath(MonitoredPathBase):
    """Schema for monitored path response."""

    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    criteria: List[Criteria] = []
    file_inventory: List["FileInventory"] = []
    storage_locations: List[ColdStorageLocation] = []

    class Config:
        from_attributes = True


class MonitoredPathSummary(MonitoredPathBase):
    """Schema for monitored path summary response."""

    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    file_count: int
    is_path_present: Optional[bool] = None
    storage_locations: List[ColdStorageLocation] = []

    class Config:
        from_attributes = True


class PathScanErrors(BaseModel):
    """Schema for path scan errors response (lazy-loaded)."""

    path_id: int
    path_name: str
    last_scan_at: Optional[datetime] = None
    last_scan_status: Optional[ScanStatus] = None
    last_scan_error_log: Optional[str] = None

    class Config:
        from_attributes = True


class FileRecordBase(BaseModel):
    """Base file record schema."""

    original_path: str
    cold_storage_path: str
    file_size: int
    operation_type: OperationType
    criteria_matched: Optional[str] = None


class FileRecord(FileRecordBase):
    """Schema for file record response."""

    id: int
    path_id: int
    moved_at: datetime

    class Config:
        from_attributes = True


class FileInventoryBase(BaseModel):
    """Base file inventory schema."""

    file_path: str
    storage_type: StorageType
    file_size: int
    file_mtime: datetime
    file_atime: Optional[datetime] = None
    file_ctime: Optional[datetime] = None
    checksum: Optional[str] = None
    file_extension: Optional[str] = None
    mime_type: Optional[str] = None
    status: FileStatus = FileStatus.ACTIVE


class FileInventoryCreate(FileInventoryBase):
    """Schema for creating file inventory entry."""

    path_id: int


class FileInventoryUpdate(BaseModel):
    """Schema for updating file inventory entry."""

    file_size: Optional[int] = None
    file_mtime: Optional[datetime] = None
    file_atime: Optional[datetime] = None
    file_ctime: Optional[datetime] = None
    checksum: Optional[str] = None
    file_extension: Optional[str] = None
    mime_type: Optional[str] = None
    status: Optional[FileStatus] = None


class FileInventory(FileInventoryBase):
    """Schema for file inventory response."""

    id: int
    path_id: int
    last_seen: datetime
    created_at: datetime
    tags: List["FileTagResponse"] = []

    class Config:
        from_attributes = True


class FileMoveRequest(BaseModel):
    """Schema for on-demand file move."""

    source_path: str
    destination_path: str
    operation_type: OperationType = OperationType.MOVE


class FileRelocateRequest(BaseModel):
    """Schema for relocating a file between cold storage locations."""

    target_storage_location_id: int = Field(
        ..., description="ID of the target cold storage location"
    )


class Statistics(BaseModel):
    """Schema for statistics response."""

    total_files_moved: int
    total_size_moved: int
    files_by_path: dict
    recent_activity: List[FileRecord]


class FileInventoryStats(BaseModel):
    """Schema for file inventory statistics."""

    total_files_hot: int
    total_files_cold: int
    total_size_hot: int
    total_size_cold: int
    files_by_path: dict
    storage_distribution: dict


class DetailedStatistics(BaseModel):
    """Comprehensive statistics with detailed metrics and trends."""

    # Capacity metrics
    total_files_moved: int
    total_size_moved: int
    total_files_hot: int
    total_files_cold: int
    total_size_hot: int
    total_size_cold: int
    space_saved: int  # Space freed from hot storage
    average_file_size: int

    # Performance metrics
    files_moved_last_24h: int
    files_moved_last_7d: int
    size_moved_last_24h: int
    size_moved_last_7d: int
    average_files_per_day: float
    average_size_per_day: float

    # Operational metrics
    total_paths: int
    active_paths: int
    total_criteria: int
    pinned_files: int

    # Trend data (last 30 days by default)
    daily_activity: list  # List of {date, files_moved, size_moved}
    storage_trend: list  # List of {date, hot_storage, cold_storage}

    # Path-specific metrics
    top_paths_by_files: list  # Top 5 paths by file count
    top_paths_by_size: list  # Top 5 paths by size


class ScanResult(BaseModel):
    """Schema for scan result."""

    path_id: int
    files_found: int
    files_moved: int
    errors: List[str] = []


class StorageStats(BaseModel):
    """Schema for storage volume statistics."""

    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    error: Optional[str] = None


class PaginatedFileInventory(BaseModel):
    """Paginated file inventory response."""

    items: List["FileInventory"]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool


class TagCreate(BaseModel):
    """Schema for creating a new tag."""

    name: str
    description: Optional[str] = None
    color: Optional[str] = None


class TagUpdate(BaseModel):
    """Schema for updating a tag."""

    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


class Tag(BaseModel):
    """Schema for tag response."""

    id: int
    name: str
    description: Optional[str]
    color: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class TagWithCount(Tag):
    """Schema for tag response with file count."""

    file_count: int = 0


class FileTagCreate(BaseModel):
    """Schema for adding a tag to a file."""

    tag_id: int
    tagged_by: Optional[str] = None


class FileTagResponse(BaseModel):
    """Schema for file tag response."""

    id: int
    file_id: int
    tag: Tag
    tagged_at: datetime
    tagged_by: Optional[str]

    class Config:
        from_attributes = True


class TagRuleCreate(BaseModel):
    """Schema for creating a tag rule."""

    tag_id: int
    criterion_type: TagRuleCriterionType
    operator: Operator
    value: str
    enabled: bool = True
    priority: int = 0


class TagRuleUpdate(BaseModel):
    """Schema for updating a tag rule."""

    criterion_type: Optional[TagRuleCriterionType] = None
    operator: Optional[Operator] = None
    value: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class TagRule(BaseModel):
    """Schema for tag rule response."""

    id: int
    tag_id: int
    tag: Tag
    criterion_type: TagRuleCriterionType
    operator: Operator
    value: str
    enabled: bool
    priority: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# Notification System Schemas


class NotifierBase(BaseModel):
    """Base notifier schema."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Human-readable name for this notifier"
    )
    type: NotifierType = Field(..., description="Type of notifier (EMAIL or GENERIC_WEBHOOK)")
    address: str = Field(..., min_length=1, description="Email address or webhook URL")
    enabled: bool = Field(True, description="Whether this notifier is active")
    subscribed_events: List[str] = Field(
        default_factory=list,
        description="List of event types to subscribe to (SCAN_COMPLETED, PATH_CREATED, etc.)",
    )

    # SMTP settings (required for email notifiers, ignored for webhooks)
    smtp_host: Optional[str] = Field(
        None, description="SMTP server hostname (required for EMAIL type)"
    )
    smtp_port: Optional[int] = Field(587, description="SMTP server port (default: 587)")
    smtp_user: Optional[str] = Field(None, description="SMTP username for authentication")
    smtp_password: Optional[str] = Field(None, description="SMTP password for authentication")
    smtp_sender: Optional[str] = Field(
        None, description="From address for emails (required for EMAIL type)"
    )
    smtp_use_tls: Optional[bool] = Field(True, description="Use TLS encryption (default: True)")

    @validator("address")
    @classmethod
    def validate_address(cls, v, values):
        """Validate address based on notifier type."""
        notifier_type = values.get("type")
        if notifier_type == NotifierType.EMAIL:
            try:
                TypeAdapter(EmailStr).validate_python(v)
            except Exception as e:
                raise ValueError(f"Invalid email address: {e}")
        elif notifier_type == NotifierType.GENERIC_WEBHOOK:
            try:
                url = HttpUrl(v)
                if url.scheme != "https":
                    raise ValueError("Webhook URLs must use HTTPS for security")
            except Exception as e:
                raise ValueError(f"Invalid webhook URL: {e}")
        return v

    @validator("subscribed_events")
    @classmethod
    def validate_event_types(cls, v):
        """Ensure all subscribed events are valid event types."""
        from app.services.notification_events import NotificationEventType

        valid_events = {e.value for e in NotificationEventType}
        invalid = set(v) - valid_events
        if invalid:
            raise ValueError(
                f"Invalid event types: {invalid}. Valid events: {', '.join(sorted(valid_events))}"
            )
        return v


class NotifierCreate(NotifierBase):
    """Schema for creating a notifier."""

    @validator("smtp_host")
    @classmethod
    def validate_smtp_host_for_email(cls, v, values):
        """Ensure smtp_host is provided for email notifiers."""
        if values.get("type") == NotifierType.EMAIL and not v:
            msg = "smtp_host is required for EMAIL notifiers"
            raise ValueError(msg)
        return v

    @validator("smtp_sender")
    @classmethod
    def validate_smtp_sender_for_email(cls, v, values):
        """Ensure smtp_sender is provided for email notifiers."""
        if values.get("type") == NotifierType.EMAIL and not v:
            msg = "smtp_sender is required for EMAIL notifiers"
            raise ValueError(msg)
        return v

    @validator("smtp_sender")
    @classmethod
    def validate_smtp_sender_format(cls, v, values):
        """Validate smtp_sender is a valid email address."""
        if v and values.get("type") == NotifierType.EMAIL:
            try:
                TypeAdapter(EmailStr).validate_python(v)
            except Exception as e:
                raise ValueError(f"Invalid sender email address: {e}") from e
        return v


class NotifierUpdate(BaseModel):
    """Schema for updating a notifier."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    type: Optional[NotifierType] = None
    address: Optional[str] = Field(None, min_length=1)
    enabled: Optional[bool] = None
    subscribed_events: Optional[List[str]] = None

    # SMTP settings (optional for updates)
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_sender: Optional[str] = None
    smtp_use_tls: Optional[bool] = None

    @validator("subscribed_events")
    @classmethod
    def validate_event_types(cls, v):
        """Ensure all subscribed events are valid event types."""
        if v is None:
            return v

        from app.services.notification_events import NotificationEventType

        valid_events = {e.value for e in NotificationEventType}
        invalid = set(v) - valid_events
        if invalid:
            raise ValueError(
                f"Invalid event types: {invalid}. Valid events: {', '.join(sorted(valid_events))}"
            )
        return v


class Notifier(NotifierBase):
    """Schema for notifier response."""

    id: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class NotificationBase(BaseModel):
    """Base notification schema."""

    level: NotificationLevel
    message: str = Field(..., min_length=1)


class NotificationCreate(NotificationBase):
    """Schema for creating a notification."""


class Notification(NotificationBase):
    """Schema for notification response."""

    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationDispatch(BaseModel):
    """Schema for notification dispatch log."""

    id: int
    notification_id: int
    notifier_id: int
    status: DispatchStatus
    details: Optional[str]
    timestamp: datetime

    class Config:
        from_attributes = True


class NotificationWithDispatches(Notification):
    """Schema for notification with dispatch history."""

    dispatches: List[NotificationDispatch] = []

    class Config:
        from_attributes = True


class TestNotifierResponse(BaseModel):
    """Schema for test notifier response."""

    success: bool
    message: str
    notifier_name: str


# Bulk Operations Schemas


class BulkFileActionRequest(BaseModel):
    """Request for bulk file operations (thaw, pin, unpin)."""

    file_ids: List[int] = Field(..., min_length=1, description="List of file inventory IDs")


class BulkFreezeRequest(BaseModel):
    """Request for bulk freeze operation."""

    file_ids: List[int] = Field(..., min_length=1, description="List of file inventory IDs")
    storage_location_id: int = Field(..., description="Target cold storage location ID")
    pin: bool = Field(False, description="Pin files after freezing")


class BulkTagRequest(BaseModel):
    """Request for bulk tag operations."""

    file_ids: List[int] = Field(..., min_length=1, description="List of file inventory IDs")
    tag_id: int = Field(..., description="Tag ID to add or remove")


class BulkActionResult(BaseModel):
    """Result for a single file in bulk operation."""

    file_id: int
    success: bool
    message: Optional[str] = None


class BulkActionResponse(BaseModel):
    """Response for bulk operations."""

    total: int
    successful: int
    failed: int
    results: List[BulkActionResult]


# Authentication Schemas


class UserCreate(BaseModel):
    """Schema for creating a new user."""

    username: str = Field(..., min_length=3, max_length=50, description="Username")
    password: str = Field(..., min_length=8, description="Password (minimum 8 characters)")


class UserLogin(BaseModel):
    """Schema for user login."""

    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class Token(BaseModel):
    """JWT token response."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")


class TokenCreate(BaseModel):
    """Schema for creating a manual API token."""

    expires_days: Optional[int] = Field(
        None, description="Token expiration in days (None = default, 0 = no expiration)"
    )


class AuthCheckResponse(BaseModel):
    """Response for authentication status check."""

    setup_required: bool = Field(..., description="Whether initial setup is required")
    user_count: int = Field(..., description="Number of users in the system")


class PasswordChange(BaseModel):
    """Schema for password change request."""

    old_password: str = Field(..., min_length=1, description="Current password")
    new_password: str = Field(..., min_length=8, description="New password (minimum 8 characters)")


# ========================================
# Remote Connection Schemas
# ========================================


class RemoteConnectionBase(BaseModel):
    """Base remote connection schema."""

    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1)


class RemoteConnectionCreate(RemoteConnectionBase):
    """Schema for creating a remote connection."""

    connection_code: str = Field(..., description="The rotating code from the remote instance")


class RemoteConnectionUpdate(BaseModel):
    """Schema for updating a remote connection."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = Field(None, min_length=1)


class RemoteConnection(RemoteConnectionBase):
    """Schema for remote connection response."""

    id: int
    remote_instance_uuid: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ChallengeRequest(BaseModel):
    """Schema for initiating a challenge-response verification."""

    initiator_uuid: str
    challenge: str  # Encrypted random hex string


class ChallengeResponse(BaseModel):
    """Schema for challenge-response result."""

    decrypted: str  # Decrypted random hex string


class RemoteTransferJobBase(BaseModel):
    """Base remote transfer job schema."""

    file_inventory_id: int
    remote_connection_id: int
    remote_monitored_path_id: int


class RemoteTransferJob(RemoteTransferJobBase):
    """Schema for remote transfer job response."""

    id: int
    status: TransferStatus
    progress: int
    current_size: int
    total_size: int
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    error_message: Optional[str]
    source_path: str
    relative_path: str
    storage_type: StorageType
    checksum: Optional[str]
    eta: Optional[float] = None  # Seconds remaining, calculated at runtime

    class Config:
        from_attributes = True


class ServerEncryptionKeyResponse(BaseModel):
    """Schema for server encryption key response."""

    id: int
    fingerprint: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ========================================
# File Browser Schemas
# ========================================


class BrowserItem(BaseModel):
    """Schema for a file or directory in the browser."""

    name: str = Field(..., description="File or directory name")
    path: str = Field(..., description="Absolute path to the file or directory")
    is_dir: bool = Field(..., description="Whether this is a directory")
    size: int = Field(0, description="File size in bytes (0 for directories)")
    modified: float = Field(..., description="Last modified timestamp")
    inventory_status: Optional[str] = Field(
        None, description="Inventory status: 'HOT', 'COLD', or None if not tracked"
    )


class BrowserResponse(BaseModel):
    """Response for directory browsing."""

    current_path: str = Field(..., description="Current directory path")
    total_items: int = Field(..., description="Total number of items in current directory")
    total_files: int = Field(0, description="Number of files in current directory")
    total_dirs: int = Field(0, description="Number of directories in current directory")
    items: List[BrowserItem] = Field(default_factory=list, description="List of items in directory")


# Rebuild models to resolve forward references
MonitoredPath.model_rebuild()
FileInventory.model_rebuild()
PaginatedFileInventory.model_rebuild()
