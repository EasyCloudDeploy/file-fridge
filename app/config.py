"""Application configuration."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""
    
    # Database
    database_url: str = "sqlite:///./file_fridge.db"
    
    # Application
    log_level: str = "INFO"
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

