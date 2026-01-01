"""Application configuration."""
from pydantic_settings import BaseSettings
from typing import Optional
from os import environ, path
from io import TextIOWrapper
import logging

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings."""
    
    # Database
    database_url: str = "sqlite:///./file_fridge.db"

    # Allow atime over network mounts (can be set via ALLOW_ATIME_OVER_NETWORK_MOUNTS env var)
    allow_atime_over_network_mounts: bool = False
    
    # Application
    log_level: str = "INFO"  # Can be overridden via LOG_LEVEL environment variable
    log_file_path: Optional[str] = None  # Optional file path for logging
    max_file_size_mb: int = 10240
    default_check_interval: int = 3600
    
    # UI
    app_name: str = "File Fridge"
    app_version: str = "0.0.0"

    f: TextIOWrapper = None
    if path.exists("VERSION"):
        try:
            with open("VERSION", "r") as f:
                app_version = f.read().strip()
        except Exception as e:
            logging.exception(f"Error reading VERSION file: {e}")
    

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

