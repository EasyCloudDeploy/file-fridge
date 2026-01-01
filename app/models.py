"""SQLAlchemy database models."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class OperationType(str, enum.Enum):
    """File operation types."""
    MOVE = "move"
    COPY = "copy"
    SYMLINK = "symlink"


class CriterionType(str, enum.Enum):
    """Criteria types for file matching."""
    MTIME = "mtime"  # Modification time
    ATIME = "atime"  # Access time
    CTIME = "ctime"  # Change time
    SIZE = "size"
    NAME = "name"
    INAME = "iname"  # Case-insensitive name
    TYPE = "type"
    PERM = "perm"
    USER = "user"
    GROUP = "group"


class Operator(str, enum.Enum):
    """Comparison operators."""
    GT = ">"
    LT = "<"
    EQ = "="
    GTE = ">="
    LTE = "<="
    CONTAINS = "contains"
    REGEX = "regex"
    MATCHES = "matches"  # For glob patterns


class MonitoredPath(Base):
    """Monitored path configuration."""
    __tablename__ = "monitored_paths"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    source_path = Column(String, nullable=False)
    cold_storage_path = Column(String, nullable=False)
    operation_type = Column(SQLEnum(OperationType), default=OperationType.MOVE)
    check_interval_seconds = Column(Integer, default=3600)
    enabled = Column(Boolean, default=True)
    prevent_indexing = Column(Boolean, default=True, nullable=False)  # Create .noindex file to prevent macOS Spotlight from corrupting timestamps
    error_message = Column(Text, nullable=True)  # Error state message (e.g., atime unavailable on network mount)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    criteria = relationship("Criteria", back_populates="path", cascade="all, delete-orphan")
    file_records = relationship("FileRecord", back_populates="path", cascade="all, delete-orphan")
    file_inventory = relationship("FileInventory", back_populates="path", cascade="all, delete-orphan")


class Criteria(Base):
    """File matching criteria."""
    __tablename__ = "criteria"
    
    id = Column(Integer, primary_key=True, index=True)
    path_id = Column(Integer, ForeignKey("monitored_paths.id"), nullable=False)
    criterion_type = Column(SQLEnum(CriterionType), nullable=False)
    operator = Column(SQLEnum(Operator), nullable=False)
    value = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    path = relationship("MonitoredPath", back_populates="criteria")


class FileRecord(Base):
    """Record of moved files."""
    __tablename__ = "file_records"

    id = Column(Integer, primary_key=True, index=True)
    path_id = Column(Integer, ForeignKey("monitored_paths.id"), nullable=True)
    original_path = Column(String, nullable=False)
    cold_storage_path = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)
    moved_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    operation_type = Column(SQLEnum(OperationType), nullable=False)
    criteria_matched = Column(Text)  # JSON string of matched criteria IDs

    path = relationship("MonitoredPath", back_populates="file_records")
    # Note: Relationship to FileInventory is handled via back-reference from FileInventory


class StorageType(str, enum.Enum):
    """Storage location types."""
    HOT = "hot"
    COLD = "cold"


class FileStatus(str, enum.Enum):
    """File status in inventory."""
    ACTIVE = "active"      # File exists and is accessible
    MOVED = "moved"        # File has been moved to cold storage
    DELETED = "deleted"    # File was deleted
    MISSING = "missing"    # File should exist but is not found


class FileInventory(Base):
    """Inventory of all files in both hot and cold storage."""
    __tablename__ = "file_inventory"

    id = Column(Integer, primary_key=True, index=True)
    path_id = Column(Integer, ForeignKey("monitored_paths.id"), nullable=False, index=True)
    file_path = Column(String, nullable=False, index=True)  # Absolute path to the file
    storage_type = Column(SQLEnum(StorageType), nullable=False, index=True)
    file_size = Column(Integer, nullable=False)
    file_mtime = Column(DateTime(timezone=True), nullable=False)  # File modification time
    file_atime = Column(DateTime(timezone=True), nullable=True)  # File access time
    file_ctime = Column(DateTime(timezone=True), nullable=True)  # File change/creation time
    checksum = Column(String, nullable=True)  # Optional checksum for change detection
    status = Column(SQLEnum(FileStatus), default=FileStatus.ACTIVE, index=True)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Composite index for common queries (path_id + storage_type + status)
    __table_args__ = (
        {'sqlite_autoincrement': True},  # For SQLite
    )

    # Relationship back to monitored path
    path = relationship("MonitoredPath", back_populates="file_inventory")

    # Note: Relationship to FileRecord can be established if needed in the future


class PinnedFile(Base):
    """Files that are pinned (excluded from future scans)."""
    __tablename__ = "pinned_files"

    id = Column(Integer, primary_key=True, index=True)
    path_id = Column(Integer, ForeignKey("monitored_paths.id"), nullable=True)
    file_path = Column(String, nullable=False, index=True)
    pinned_at = Column(DateTime(timezone=True), server_default=func.now())
    pinned_by = Column(String, nullable=True)  # Optional: who/what pinned it

    path = relationship("MonitoredPath")

