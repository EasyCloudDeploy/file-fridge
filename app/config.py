"""Application configuration."""
from pydantic_settings import BaseSettings
from typing import Optional
from os import environ


class Settings(BaseSettings):
    """Application settings."""
    
    # Database
    database_url: str = "sqlite:///./file_fridge.db"

    # Allow atime over network mounts (can be set via ALLOW_ATIME_OVER_NETWORK_MOUNTS env var)
    allow_atime_over_network_mounts: bool = False
    
    # Application
    log_level: str = "INFO"  # Can be overridden via LOG_LEVEL environment variable
    max_file_size_mb: int = 10240
    default_check_interval: int = 3600
    
    # UI
    app_name: str = "File Fridge"
    app_version: str = "1.0.0"
    
    # Session
    secret_key: str = "file-fridge-secret-key-change-in-production"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

