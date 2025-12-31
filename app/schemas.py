"""Pydantic schemas for API validation."""
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from app.models import OperationType, CriterionType, Operator


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


class MonitoredPath(MonitoredPathBase):
    """Schema for monitored path response."""
    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    criteria: List[Criteria] = []
    
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


class ScanResult(BaseModel):
    """Schema for scan result."""
    path_id: int
    files_found: int
    files_moved: int
    errors: List[str] = []

