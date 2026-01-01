"""Application configuration."""
from pydantic_settings import BaseSettings
from pydantic import model_validator, computed_field
from typing import Optional
from os import path
import logging

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
        else:
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

    # UI
    # Override via APP_NAME environment variable
    app_name: str = "File Fridge"

    # Override via APP_VERSION environment variable
    # If not set, will read from VERSION file if it exists
    app_version: str = "0.0.0"

    @model_validator(mode='after')
    def read_version_file(self):
        """Read version from VERSION file if app_version is still default and file exists."""
        if self.app_version == "0.0.0" and path.exists("VERSION"):
            try:
                with open("VERSION", "r") as f:
                    self.app_version = f.read().strip()
            except Exception as e:
                logger.exception(f"Error reading VERSION file: {e}")
        return self

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

