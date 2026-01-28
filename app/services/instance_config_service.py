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
                "source": "environment" if settings.ff_instance_url else "database" if metadata and metadata.instance_url else "not_set",
                "env_value": settings.ff_instance_url,
                "db_value": metadata.instance_url if metadata else None,
                "can_edit": not bool(settings.ff_instance_url),  # Can only edit if not set in env
            },
            "instance_name": {
                "value": self.get_instance_name(db),
                "source": "environment" if settings.instance_name else "database" if metadata and metadata.instance_name else "not_set",
                "env_value": settings.instance_name,
                "db_value": metadata.instance_name if metadata else None,
                "can_edit": not bool(settings.instance_name),  # Can only edit if not set in env
            },
        }


instance_config_service = InstanceConfigService()
