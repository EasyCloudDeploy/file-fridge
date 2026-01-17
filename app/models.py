"""SQLAlchemy database models."""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

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


class ScanStatus(str, enum.Enum):
    """Scan execution status."""

    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"


# Association table for many-to-many relationship between MonitoredPath and ColdStorageLocation
path_storage_location_association = Table(
    "path_storage_location_association",
    Base.metadata,
    Column("path_id", Integer, ForeignKey("monitored_paths.id"), primary_key=True),
    Column(
        "storage_location_id", Integer, ForeignKey("cold_storage_locations.id"), primary_key=True
    ),
)


class ColdStorageLocation(Base):
    """Cold storage location configuration."""

    __tablename__ = "cold_storage_locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    path = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship to monitored paths
    paths = relationship(
        "MonitoredPath",
        secondary=path_storage_location_association,
        back_populates="storage_locations",
    )


class MonitoredPath(Base):
    """Monitored path configuration."""

    __tablename__ = "monitored_paths"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    source_path = Column(String, nullable=False)
    operation_type = Column(SQLEnum(OperationType), default=OperationType.MOVE)
    check_interval_seconds = Column(Integer, default=3600)
    enabled = Column(Boolean, default=True)
    prevent_indexing = Column(
        Boolean, default=True, nullable=False
    )  # Create .noindex file to prevent macOS Spotlight from corrupting timestamps
    error_message = Column(
        Text, nullable=True
    )  # Error state message (e.g., atime unavailable on network mount)
    last_scan_at = Column(DateTime(timezone=True), nullable=True)  # When the last scan finished
    last_scan_status = Column(
        SQLEnum(ScanStatus), nullable=True
    )  # Status of the last scan (SUCCESS, FAILURE, PENDING)
    last_scan_error_log = Column(Text, nullable=True)  # Full error log from the last scan
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    criteria = relationship("Criteria", back_populates="path", cascade="all, delete-orphan")
    file_records = relationship("FileRecord", back_populates="path", cascade="all, delete-orphan")
    file_inventory = relationship(
        "FileInventory", back_populates="path", cascade="all, delete-orphan"
    )
    storage_locations = relationship(
        "ColdStorageLocation", secondary=path_storage_location_association, back_populates="paths"
    )

    @property
    def cold_storage_path(self) -> str:
        """
        Compatibility property that returns the first storage location's path.
        This is a temporary measure for backward compatibility with existing services.

        TODO: Refactor all services to handle multiple storage locations properly:
        - file_scanner.py: Scan all storage locations for inventory
        - file_reconciliation.py: Check all storage locations
        - criteria.py: Validate atime for all storage locations
        - utils/indexing.py: Manage .noindex for all locations
        """
        if self.storage_locations:
            return self.storage_locations[0].path
        msg = f"Path '{self.name}' has no storage locations configured"
        raise ValueError(msg)


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
    cold_storage_location_id = Column(
        Integer, ForeignKey("cold_storage_locations.id"), nullable=True, index=True
    )
    file_size = Column(Integer, nullable=False)
    moved_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    operation_type = Column(SQLEnum(OperationType), nullable=False)
    criteria_matched = Column(Text)  # JSON string of matched criteria IDs

    path = relationship("MonitoredPath", back_populates="file_records")
    storage_location = relationship("ColdStorageLocation")
    # Note: Relationship to FileInventory is handled via back-reference from FileInventory


class StorageType(str, enum.Enum):
    """Storage location types."""

    HOT = "hot"
    COLD = "cold"


class FileStatus(str, enum.Enum):
    """File status in inventory."""

    ACTIVE = "active"  # File exists and is accessible
    MOVED = "moved"  # File has been moved to cold storage
    DELETED = "deleted"  # File was deleted
    MISSING = "missing"  # File should exist but is not found
    MIGRATING = "migrating"  # File is being relocated between cold storage locations


class FileInventory(Base):
    """Inventory of all files in both hot and cold storage."""

    __tablename__ = "file_inventory"

    id = Column(Integer, primary_key=True, index=True)
    path_id = Column(Integer, ForeignKey("monitored_paths.id"), nullable=False, index=True)
    file_path = Column(String, nullable=False, index=True)  # Absolute path to the file
    storage_type = Column(SQLEnum(StorageType), nullable=False, index=True)
    file_size = Column(Integer, nullable=False, index=True)  # Indexed for sorting
    file_mtime = Column(DateTime(timezone=True), nullable=False, index=True)  # Indexed for sorting
    file_atime = Column(DateTime(timezone=True), nullable=True, index=True)  # Indexed for sorting
    file_ctime = Column(DateTime(timezone=True), nullable=True)  # File change/creation time
    checksum = Column(
        String, nullable=True, index=True
    )  # SHA256 hash for deduplication and verification
    file_extension = Column(String, nullable=True, index=True)  # File extension (e.g., .pdf, .jpg)
    mime_type = Column(String, nullable=True)  # MIME type (e.g., application/pdf)
    status = Column(SQLEnum(FileStatus), default=FileStatus.ACTIVE, index=True)
    last_seen = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    cold_storage_location_id = Column(
        Integer, ForeignKey("cold_storage_locations.id"), nullable=True, index=True
    )

    # Composite indexes for common query patterns
    __table_args__ = (
        # Index for filtering by path, storage type, and status (common query pattern)
        Index("idx_inventory_path_storage_status", "path_id", "storage_type", "status"),
        # Index for filtering by storage type and status with sorting by last_seen
        Index("idx_inventory_storage_status_lastseen", "storage_type", "status", "last_seen"),
        # Index for searching by file extension
        Index("idx_inventory_extension", "file_extension"),
        {"sqlite_autoincrement": True},  # For SQLite
    )

    # Relationship back to monitored path
    path = relationship("MonitoredPath", back_populates="file_inventory")

    # Relationship to cold storage location
    storage_location = relationship("ColdStorageLocation")

    # Relationship to tags
    tags = relationship("FileTag", back_populates="file", cascade="all, delete-orphan")


class PinnedFile(Base):
    """Files that are pinned (excluded from future scans)."""

    __tablename__ = "pinned_files"

    id = Column(Integer, primary_key=True, index=True)
    path_id = Column(Integer, ForeignKey("monitored_paths.id"), nullable=True)
    file_path = Column(String, nullable=False, index=True)
    pinned_at = Column(DateTime(timezone=True), server_default=func.now())
    pinned_by = Column(String, nullable=True)  # Optional: who/what pinned it

    path = relationship("MonitoredPath")


class Tag(Base):
    """User-defined tags for organizing files."""

    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    description = Column(String, nullable=True)
    color = Column(String, nullable=True)  # Hex color code for UI display (e.g., #FF5733)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to file tags
    file_tags = relationship("FileTag", back_populates="tag", cascade="all, delete-orphan")


class FileTag(Base):
    """Association table linking files to tags."""

    __tablename__ = "file_tags"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("file_inventory.id"), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False, index=True)
    tagged_at = Column(DateTime(timezone=True), server_default=func.now())
    tagged_by = Column(String, nullable=True)  # Optional: who added the tag

    # Composite index for unique file-tag pairs
    __table_args__ = (
        Index("idx_file_tag_unique", "file_id", "tag_id", unique=True),
        {"sqlite_autoincrement": True},
    )

    # Relationships
    file = relationship("FileInventory", back_populates="tags")
    tag = relationship("Tag", back_populates="file_tags")


class TagRuleCriterionType(str, enum.Enum):
    """Criteria types for automatic tag rules."""

    EXTENSION = "extension"  # File extension (e.g., .pdf, .jpg)
    PATH_PATTERN = "path_pattern"  # Path pattern matching (glob or regex)
    MIME_TYPE = "mime_type"  # MIME type (e.g., image/*, application/pdf)
    SIZE = "size"  # File size comparisons
    NAME_PATTERN = "name_pattern"  # Filename pattern (not full path)


class TagRule(Base):
    """Automated rules for applying tags to files based on criteria."""

    __tablename__ = "tag_rules"

    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False, index=True)
    criterion_type = Column(SQLEnum(TagRuleCriterionType), nullable=False)
    operator = Column(SQLEnum(Operator), nullable=False)
    value = Column(String, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)  # Higher priority = applied first
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    tag = relationship("Tag")


class NotifierType(str, enum.Enum):
    """Notification destination types."""

    EMAIL = "email"
    GENERIC_WEBHOOK = "generic_webhook"


class NotificationLevel(str, enum.Enum):
    """Notification severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DispatchStatus(str, enum.Enum):
    """Notification dispatch status."""

    SUCCESS = "success"
    FAILED = "failed"


class Notifier(Base):
    """Configured notification destination."""

    __tablename__ = "notifiers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    type = Column(SQLEnum(NotifierType), nullable=False)
    address = Column(String, nullable=False)  # Email address or webhook URL
    enabled = Column(Boolean, default=True, nullable=False)
    filter_level = Column(
        SQLEnum(NotificationLevel), default=NotificationLevel.INFO, nullable=False
    )

    # SMTP settings (for email notifiers only)
    smtp_host = Column(String, nullable=True)  # SMTP server hostname
    smtp_port = Column(Integer, nullable=True)  # SMTP server port (default 587)
    smtp_user = Column(String, nullable=True)  # SMTP username
    smtp_password = Column(String, nullable=True)  # SMTP password (stored encrypted in production)
    smtp_sender = Column(String, nullable=True)  # From address
    smtp_use_tls = Column(Boolean, default=True, nullable=True)  # Use TLS encryption

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    dispatches = relationship(
        "NotificationDispatch", back_populates="notifier", cascade="all, delete-orphan"
    )


class Notification(Base):
    """Notification event record."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(SQLEnum(NotificationLevel), nullable=False, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Relationships
    dispatches = relationship(
        "NotificationDispatch", back_populates="notification", cascade="all, delete-orphan"
    )


class NotificationDispatch(Base):
    """Log of notification dispatch attempts."""

    __tablename__ = "notification_dispatches"

    id = Column(Integer, primary_key=True, index=True)
    notification_id = Column(Integer, ForeignKey("notifications.id"), nullable=False, index=True)
    notifier_id = Column(Integer, ForeignKey("notifiers.id"), nullable=False, index=True)
    status = Column(SQLEnum(DispatchStatus), nullable=False)
    details = Column(Text, nullable=True)  # Error message if failed
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Relationships
    notification = relationship("Notification", back_populates="dispatches")
    notifier = relationship("Notifier", back_populates="dispatches")


class User(Base):
    """User account for authentication."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True, nullable=False)
