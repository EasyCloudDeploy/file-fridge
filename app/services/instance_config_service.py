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

    def get_oidc_enabled(self, db: Session) -> bool:
        if settings.oidc_enabled is not None:
            return settings.oidc_enabled
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_enabled is not None:
            return metadata.oidc_enabled
        return False

    def get_oidc_issuer(self, db: Session) -> Optional[str]:
        if settings.oidc_issuer is not None:
            return settings.oidc_issuer
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_issuer:
            return metadata.oidc_issuer
        return None

    def get_oidc_client_id(self, db: Session) -> Optional[str]:
        if settings.oidc_client_id is not None:
            return settings.oidc_client_id
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_client_id:
            return metadata.oidc_client_id
        return None

    def get_oidc_client_secret(self, db: Session) -> Optional[str]:
        if settings.oidc_client_secret is not None:
            return settings.oidc_client_secret
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_client_secret:
            return metadata.oidc_client_secret
        return None

    def get_oidc_redirect_uri(self, db: Session) -> Optional[str]:
        if settings.oidc_redirect_uri is not None:
            return settings.oidc_redirect_uri
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_redirect_uri:
            return metadata.oidc_redirect_uri
        return None

    def get_oidc_provider_name(self, db: Session) -> str:
        if settings.oidc_provider_name is not None:
            return settings.oidc_provider_name
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_provider_name:
            return metadata.oidc_provider_name
        return "Authentik"

    def get_oidc_roles_claim(self, db: Session) -> str:
        if settings.oidc_roles_claim is not None:
            return settings.oidc_roles_claim
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_roles_claim:
            return metadata.oidc_roles_claim
        return "roles"

    def get_oidc_admin_group(self, db: Session) -> str:
        if settings.oidc_admin_group is not None:
            return settings.oidc_admin_group
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_admin_group:
            return metadata.oidc_admin_group
        return "admin"

    def get_oidc_manager_group(self, db: Session) -> str:
        if settings.oidc_manager_group is not None:
            return settings.oidc_manager_group
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_manager_group:
            return metadata.oidc_manager_group
        return "manager"

    def get_oidc_viewer_group(self, db: Session) -> str:
        if settings.oidc_viewer_group is not None:
            return settings.oidc_viewer_group
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_viewer_group:
            return metadata.oidc_viewer_group
        return "viewer"

    def get_oidc_default_roles(self, db: Session) -> str:
        if settings.oidc_default_roles is not None:
            return settings.oidc_default_roles
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.oidc_default_roles:
            return metadata.oidc_default_roles
        return "viewer"

    def is_oidc_env_configured(self) -> bool:
        return (
            settings.oidc_enabled is not None
            or settings.oidc_issuer is not None
            or settings.oidc_client_id is not None
            or settings.oidc_client_secret is not None
        )

    def set_oidc_config(
        self,
        db: Session,
        oidc_enabled: bool,
        oidc_client_id: Optional[str],
        oidc_client_secret: Optional[str],
        oidc_issuer: Optional[str],
        oidc_redirect_uri: Optional[str],
        oidc_provider_name: Optional[str],
        oidc_roles_claim: Optional[str],
        oidc_admin_group: Optional[str],
        oidc_manager_group: Optional[str],
        oidc_viewer_group: Optional[str],
        oidc_default_roles: Optional[str],
    ) -> InstanceMetadata:
        metadata = db.query(InstanceMetadata).first()
        if not metadata:
            import uuid
            metadata = InstanceMetadata(instance_uuid=str(uuid.uuid4()))
            db.add(metadata)

        metadata.oidc_enabled = oidc_enabled
        metadata.oidc_client_id = oidc_client_id
        if oidc_client_secret is not None:
            metadata.oidc_client_secret = oidc_client_secret
        metadata.oidc_issuer = oidc_issuer
        metadata.oidc_redirect_uri = oidc_redirect_uri
        metadata.oidc_provider_name = oidc_provider_name or "Authentik"
        metadata.oidc_roles_claim = oidc_roles_claim or "roles"
        metadata.oidc_admin_group = oidc_admin_group or "admin"
        metadata.oidc_manager_group = oidc_manager_group or "manager"
        metadata.oidc_viewer_group = oidc_viewer_group or "viewer"
        metadata.oidc_default_roles = oidc_default_roles or "viewer"

        db.commit()
        db.refresh(metadata)
        logger.info("Global OIDC settings updated in database")
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
                    "source": (
                        "environment"
                        if settings.smtp_host
                        else "database" if metadata and metadata.smtp_host else "not_set"
                    ),
                    "env_value": settings.smtp_host,
                    "db_value": metadata.smtp_host if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "smtp_port": {
                    "value": self.get_smtp_port(db),
                    "source": (
                        "environment"
                        if settings.smtp_host
                        else (
                            "database" if metadata and metadata.smtp_port is not None else "not_set"
                        )
                    ),
                    "env_value": settings.smtp_port,
                    "db_value": metadata.smtp_port if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "smtp_user": {
                    "value": self.get_smtp_user(db),
                    "source": (
                        "environment"
                        if settings.smtp_host
                        else "database" if metadata and metadata.smtp_user else "not_set"
                    ),
                    "env_value": settings.smtp_user,
                    "db_value": metadata.smtp_user if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "smtp_sender": {
                    "value": self.get_smtp_sender(db),
                    "source": (
                        "environment"
                        if settings.smtp_sender
                        else "database" if metadata and metadata.smtp_sender else "not_set"
                    ),
                    "env_value": settings.smtp_sender,
                    "db_value": metadata.smtp_sender if metadata else None,
                    "can_edit": not bool(settings.smtp_sender),
                },
                "smtp_use_tls": {
                    "value": self.get_smtp_use_tls(db),
                    "source": (
                        "environment"
                        if settings.smtp_host
                        else (
                            "database"
                            if metadata and metadata.smtp_use_tls is not None
                            else "not_set"
                        )
                    ),
                    "env_value": settings.smtp_use_tls,
                    "db_value": metadata.smtp_use_tls if metadata else None,
                    "can_edit": not bool(settings.smtp_host),
                },
                "is_env_configured": bool(settings.smtp_host),
            },
            "oidc": {
                "oidc_enabled": {
                    "value": self.get_oidc_enabled(db),
                    "source": (
                        "environment"
                        if settings.oidc_enabled is not None
                        else "database" if metadata and metadata.oidc_enabled is not None else "not_set"
                    ),
                    "env_value": settings.oidc_enabled,
                    "db_value": metadata.oidc_enabled if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_client_id": {
                    "value": self.get_oidc_client_id(db),
                    "source": (
                        "environment"
                        if settings.oidc_client_id is not None
                        else "database" if metadata and metadata.oidc_client_id else "not_set"
                    ),
                    "env_value": settings.oidc_client_id,
                    "db_value": metadata.oidc_client_id if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_issuer": {
                    "value": self.get_oidc_issuer(db),
                    "source": (
                        "environment"
                        if settings.oidc_issuer is not None
                        else "database" if metadata and metadata.oidc_issuer else "not_set"
                    ),
                    "env_value": settings.oidc_issuer,
                    "db_value": metadata.oidc_issuer if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_redirect_uri": {
                    "value": self.get_oidc_redirect_uri(db),
                    "source": (
                        "environment"
                        if settings.oidc_redirect_uri is not None
                        else "database" if metadata and metadata.oidc_redirect_uri else "not_set"
                    ),
                    "env_value": settings.oidc_redirect_uri,
                    "db_value": metadata.oidc_redirect_uri if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_provider_name": {
                    "value": self.get_oidc_provider_name(db),
                    "source": (
                        "environment"
                        if settings.oidc_provider_name is not None
                        else "database" if metadata and metadata.oidc_provider_name else "not_set"
                    ),
                    "env_value": settings.oidc_provider_name,
                    "db_value": metadata.oidc_provider_name if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_roles_claim": {
                    "value": self.get_oidc_roles_claim(db),
                    "source": (
                        "environment"
                        if settings.oidc_roles_claim is not None
                        else "database" if metadata and metadata.oidc_roles_claim else "not_set"
                    ),
                    "env_value": settings.oidc_roles_claim,
                    "db_value": metadata.oidc_roles_claim if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_admin_group": {
                    "value": self.get_oidc_admin_group(db),
                    "source": (
                        "environment"
                        if settings.oidc_admin_group is not None
                        else "database" if metadata and metadata.oidc_admin_group else "not_set"
                    ),
                    "env_value": settings.oidc_admin_group,
                    "db_value": metadata.oidc_admin_group if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_manager_group": {
                    "value": self.get_oidc_manager_group(db),
                    "source": (
                        "environment"
                        if settings.oidc_manager_group is not None
                        else "database" if metadata and metadata.oidc_manager_group else "not_set"
                    ),
                    "env_value": settings.oidc_manager_group,
                    "db_value": metadata.oidc_manager_group if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_viewer_group": {
                    "value": self.get_oidc_viewer_group(db),
                    "source": (
                        "environment"
                        if settings.oidc_viewer_group is not None
                        else "database" if metadata and metadata.oidc_viewer_group else "not_set"
                    ),
                    "env_value": settings.oidc_viewer_group,
                    "db_value": metadata.oidc_viewer_group if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "oidc_default_roles": {
                    "value": self.get_oidc_default_roles(db),
                    "source": (
                        "environment"
                        if settings.oidc_default_roles is not None
                        else "database" if metadata and metadata.oidc_default_roles else "not_set"
                    ),
                    "env_value": settings.oidc_default_roles,
                    "db_value": metadata.oidc_default_roles if metadata else None,
                    "can_edit": not self.is_oidc_env_configured(),
                },
                "is_env_configured": self.is_oidc_env_configured(),
            },
        }


instance_config_service = InstanceConfigService()
