"""Service for managing instance configuration with database fallback."""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import InstanceMetadata

logger = logging.getLogger(__name__)


class InstanceConfigService:
    """Manages instance configuration with environment variable priority and database fallback."""

    def get_instance_url(self, db: Session) -> Optional[str]:
        """
        Get the instance URL.

        Priority:
        1. Environment variable FF_INSTANCE_URL
        2. Database value from InstanceMetadata

        Args:
            db: Database session

        Returns:
            Instance URL or None if not configured
        """
        # Priority 1: Environment variable
        if settings.ff_instance_url:
            return settings.ff_instance_url

        # Priority 2: Database
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.instance_url:
            return metadata.instance_url

        return None

    def get_instance_name(self, db: Session) -> Optional[str]:
        """
        Get the instance name.

        Priority:
        1. Environment variable INSTANCE_NAME
        2. Database value from InstanceMetadata

        Args:
            db: Database session

        Returns:
            Instance name or None if not configured
        """
        # Priority 1: Environment variable
        if settings.instance_name:
            return settings.instance_name

        # Priority 2: Database
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.instance_name:
            return metadata.instance_name

        return None

    def set_instance_url(self, db: Session, url: Optional[str]) -> InstanceMetadata:
        """
        Set the instance URL in the database.

        Note: This does NOT override the environment variable if set.
        The environment variable always takes precedence.

        Args:
            db: Database session
            url: Instance URL to set (or None to clear)

        Returns:
            Updated InstanceMetadata object
        """
        metadata = db.query(InstanceMetadata).first()
        if not metadata:
            # This should not happen as InstanceMetadata is created on startup
            # but handle it gracefully
            import uuid

            metadata = InstanceMetadata(instance_uuid=str(uuid.uuid4()))
            db.add(metadata)

        metadata.instance_url = url
        db.commit()
        db.refresh(metadata)

        logger.info(f"Instance URL updated in database: {url}")
        return metadata

    def set_instance_name(self, db: Session, name: Optional[str]) -> InstanceMetadata:
        """
        Set the instance name in the database.

        Note: This does NOT override the environment variable if set.
        The environment variable always takes precedence.

        Args:
            db: Database session
            name: Instance name to set (or None to clear)

        Returns:
            Updated InstanceMetadata object
        """
        metadata = db.query(InstanceMetadata).first()
        if not metadata:
            import uuid

            metadata = InstanceMetadata(instance_uuid=str(uuid.uuid4()))
            db.add(metadata)

        metadata.instance_name = name
        db.commit()
        db.refresh(metadata)

        logger.info(f"Instance name updated in database: {name}")
        return metadata

    def get_smtp_host(self, db: Session) -> Optional[str]:
        if settings.smtp_host:
            return settings.smtp_host
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.smtp_host:
            return metadata.smtp_host
        return None

    def get_smtp_port(self, db: Session) -> int:
        if settings.smtp_host:
            return settings.smtp_port
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.smtp_port is not None:
            return metadata.smtp_port
        return 587

    def get_smtp_user(self, db: Session) -> Optional[str]:
        if settings.smtp_host:
            return settings.smtp_user
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.smtp_user:
            return metadata.smtp_user
        return None

    def get_smtp_password(self, db: Session) -> Optional[str]:
        if settings.smtp_host:
            return settings.smtp_password
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.smtp_password:
            return metadata.smtp_password
        return None

    def get_smtp_sender(self, db: Session) -> Optional[str]:
        if settings.smtp_sender:
            return settings.smtp_sender
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.smtp_sender:
            return metadata.smtp_sender
        return None

    def get_smtp_use_tls(self, db: Session) -> bool:
        if settings.smtp_host:
            return settings.smtp_use_tls
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.smtp_use_tls is not None:
            return metadata.smtp_use_tls
        return True

    def set_smtp_config(
        self,
        db: Session,
        smtp_host: Optional[str],
        smtp_port: int,
        smtp_user: Optional[str],
        smtp_password: Optional[str],
        smtp_sender: Optional[str],
        smtp_use_tls: bool,
    ) -> InstanceMetadata:
        metadata = db.query(InstanceMetadata).first()
        if not metadata:
            import uuid
            metadata = InstanceMetadata(instance_uuid=str(uuid.uuid4()))
            db.add(metadata)

        metadata.smtp_host = smtp_host
        metadata.smtp_port = smtp_port
        metadata.smtp_user = smtp_user
        if smtp_password is not None:
            metadata.smtp_password = smtp_password
        metadata.smtp_sender = smtp_sender
        metadata.smtp_use_tls = smtp_use_tls

        db.commit()
        db.refresh(metadata)
        logger.info("Global SMTP settings updated in database")
        return metadata

    def get_config_info(self, db: Session) -> dict:
        """
        Get information about where configuration values are coming from.

        Args:
            db: Database session

        Returns:
            Dictionary with configuration info
        """
        metadata = db.query(InstanceMetadata).first()

        return {
            "instance_url": {
                "value": self.get_instance_url(db),
                "source": (
                    "environment"
                    if settings.ff_instance_url
                    else "database" if metadata and metadata.instance_url else "not_set"
                ),
                "env_value": settings.ff_instance_url,
                "db_value": metadata.instance_url if metadata else None,
                "can_edit": not bool(settings.ff_instance_url),  # Can only edit if not set in env
            },
            "instance_name": {
                "value": self.get_instance_name(db),
                "source": (
                    "environment"
                    if settings.instance_name
                    else "database" if metadata and metadata.instance_name else "not_set"
                ),
                "env_value": settings.instance_name,
                "db_value": metadata.instance_name if metadata else None,
                "can_edit": not bool(settings.instance_name),  # Can only edit if not set in env
            },
            "smtp": {
                "smtp_host": {
                    "value": self.get_smtp_host(db),
                    "source": "environment" if settings.smtp_host else "database" if metadata and metadata.smtp_host else "not_set",
                    "env_value": settings.smtp_host,
                    "db_value": metadata.smtp_host if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "smtp_port": {
                    "value": self.get_smtp_port(db),
                    "source": "environment" if settings.smtp_host else "database" if metadata and metadata.smtp_port is not None else "not_set",
                    "env_value": settings.smtp_port,
                    "db_value": metadata.smtp_port if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "smtp_user": {
                    "value": self.get_smtp_user(db),
                    "source": "environment" if settings.smtp_host else "database" if metadata and metadata.smtp_user else "not_set",
                    "env_value": settings.smtp_user,
                    "db_value": metadata.smtp_user if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "smtp_sender": {
                    "value": self.get_smtp_sender(db),
                    "source": "environment" if settings.smtp_sender else "database" if metadata and metadata.smtp_sender else "not_set",
                    "env_value": settings.smtp_sender,
                    "db_value": metadata.smtp_sender if metadata else None,
                    "can_edit": not bool(settings.smtp_sender),
                },
                "smtp_use_tls": {
                    "value": self.get_smtp_use_tls(db),
                    "source": "environment" if settings.smtp_host else "database" if metadata and metadata.smtp_use_tls is not None else "not_set",
                    "env_value": settings.smtp_use_tls,
                    "db_value": metadata.smtp_use_tls if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "is_env_configured": bool(settings.smtp_host),
            }
        }


instance_config_service = InstanceConfigService()
