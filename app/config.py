"""Application configuration."""
import logging
import logging
from pathlib import Path
from typing import Optional

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings.

    All settings can be overridden via environment variables.
    Environment variable names are case-insensitive.
    """

    # Database - just the file path, SQLite protocol is added automatically
    # Override via DATABASE_PATH environment variable
    database_path: str = "./data/file_fridge.db"

    @computed_field
    @property
    def database_url(self) -> str:
        """Convert database path to SQLite URL format."""
        # Already a full URL (for backward compatibility)
        if self.database_path.startswith("sqlite:///"):
            return self.database_path

        # Convert file path to SQLite URL
        # Relative paths: ./data/file.db -> sqlite:///./data/file.db
        # Absolute paths: /abs/path/file.db -> sqlite:////abs/path/file.db
        if self.database_path.startswith("/"):
            # Absolute path needs four slashes
            return f"sqlite:///{self.database_path}"
        # Relative path needs three slashes
        return f"sqlite:///{self.database_path}"

    # Allow atime over network mounts
    # Override via ALLOW_ATIME_OVER_NETWORK_MOUNTS environment variable
    allow_atime_over_network_mounts: bool = False

    # Application
    # Override via LOG_LEVEL environment variable
    log_level: str = "INFO"

    # Optional file path for logging
    # Override via LOG_FILE_PATH environment variable
    log_file_path: Optional[str] = None

    # Override via MAX_FILE_SIZE_MB environment variable
    max_file_size_mb: int = 10240

    # Override via DEFAULT_CHECK_INTERVAL environment variable
    default_check_interval: int = 3600

    # Path prefix translation for containerized environments
    # Override via CONTAINER_PATH_PREFIX environment variable
    # This is the path prefix as seen inside the container (e.g., "/data")
    container_path_prefix: Optional[str] = None

    # Override via HOST_PATH_PREFIX environment variable
    # This is the path prefix as seen on the host (e.g., "/mnt/data")
    # Used when creating symlinks to ensure they work from the host perspective
    host_path_prefix: Optional[str] = None

    # UI
    # Override via APP_NAME environment variable
    app_name: str = "File Fridge"

    # Override via APP_VERSION environment variable
    # If not set, will read from VERSION file if it exists
    app_version: str = "0.0.0"

    # Statistics retention period in days
    # Override via STATS_RETENTION_DAYS environment variable
    # FileRecord entries older than this will be automatically deleted
    stats_retention_days: int = 30

    @model_validator(mode="after")
    def read_version_file(self):
        """Read version from VERSION file if app_version is still default and file exists."""
        version_file = Path("VERSION")
        if self.app_version == "0.0.0" and version_file.exists():
            try:
                with version_file.open("r") as f:
                    self.app_version = f.read().strip()
            except Exception as e:
                logger.exception("Error reading VERSION file", exc_info=e)
        return self

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


def translate_path_for_symlink(container_path: str) -> str:
    """
    Translate a container path to a host path for symlink creation.

    This is needed when running in Docker: symlinks must point to paths
    as they appear on the host system, not as they appear in the container.

    Example:
        Container sees: /data/cold_storage/file.txt
        Host sees: /mnt/data/cold_storage/file.txt

        container_path_prefix = "/data"
        host_path_prefix = "/mnt/data"

        Result: /mnt/data/cold_storage/file.txt

    Args:
        container_path: Path as seen inside the container

    Returns:
        Path as it should appear on the host (for symlinks)
    """
    # If no translation configured, return as-is
    if not settings.container_path_prefix or not settings.host_path_prefix:
        return container_path

    # Normalize prefixes (remove trailing slashes)
    container_prefix = settings.container_path_prefix.rstrip("/")
    host_prefix = settings.host_path_prefix.rstrip("/")

    # If path starts with container prefix, replace with host prefix
    if container_path.startswith(container_prefix):
        # Replace the prefix
        relative_path = container_path[len(container_prefix):]
        return host_prefix + relative_path

    # Path doesn't match container prefix, return as-is
    return container_path