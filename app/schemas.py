"""Pydantic schemas for API validation."""
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from app.models import OperationType, CriterionType, Operator, StorageType, FileStatus, TagRuleCriterionType


class CriteriaBase(BaseModel):
    """Base criteria schema."""
    criterion_type: CriterionType
    operator: Operator
    value: str
    enabled: bool = True


class CriteriaCreate(CriteriaBase):
    """Schema for creating criteria."""
    pass


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


class MonitoredPathBase(BaseModel):
    """Base monitored path schema."""
    name: str = Field(..., min_length=1, max_length=255)
    source_path: str = Field(..., min_length=1)
    cold_storage_path: str = Field(..., min_length=1)
    operation_type: OperationType = OperationType.MOVE
    check_interval_seconds: int = Field(..., ge=60)  # Minimum 1 minute
    enabled: bool = True
    prevent_indexing: bool = True  # Create .noindex file to prevent macOS Spotlight from corrupting timestamps
    error_message: str | None = None  # Error state message


class MonitoredPathCreate(MonitoredPathBase):
    """Schema for creating monitored path."""
    pass


class MonitoredPathUpdate(BaseModel):
    """Schema for updating monitored path."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    source_path: Optional[str] = Field(None, min_length=1)
    cold_storage_path: Optional[str] = Field(None, min_length=1)
    operation_type: Optional[OperationType] = None
    check_interval_seconds: Optional[int] = Field(None, ge=60)
    enabled: Optional[bool] = None
    prevent_indexing: Optional[bool] = None


class MonitoredPath(MonitoredPathBase):
    """Schema for monitored path response."""
    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    criteria: List[Criteria] = []
    file_inventory: List["FileInventory"] = []

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

