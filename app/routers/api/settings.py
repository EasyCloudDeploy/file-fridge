"""API routes for system settings management."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import OIDCSettingsUpdate, SMTPSettingsUpdate
from app.security import PermissionChecker
from app.services.instance_config_service import instance_config_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/config")
def get_config(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker("notifiers"))],
):
    """Get global configuration and SMTP source information."""
    return instance_config_service.get_config_info(db)


@router.put("/smtp")
def update_smtp_config(
    smtp_data: SMTPSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker("notifiers"))],
):
    """Update global SMTP server settings."""
    config_info = instance_config_service.get_config_info(db)
    # Check if SMTP is configured via environment variables
    if config_info.get("smtp", {}).get("is_env_configured", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP is configured via environment variables and cannot be edited via the API",
        )

    instance_config_service.set_smtp_config(
        db=db,
        smtp_host=smtp_data.smtp_host,
        smtp_port=smtp_data.smtp_port or 587,
        smtp_user=smtp_data.smtp_user,
        smtp_password=smtp_data.smtp_password,
        smtp_sender=smtp_data.smtp_sender,
        smtp_use_tls=smtp_data.smtp_use_tls if smtp_data.smtp_use_tls is not None else True,
    )
    return {"message": "SMTP configuration updated successfully"}


@router.put("/oidc")
def update_oidc_config(
    oidc_data: OIDCSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker("notifiers"))],
):
    """Update global OIDC settings."""
    config_info = instance_config_service.get_config_info(db)
    # Check if OIDC is configured via environment variables
    if config_info.get("oidc", {}).get("is_env_configured", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC is configured via environment variables and cannot be edited via the API",
        )

    # Validate issuer & client_id if enabled
    if oidc_data.oidc_enabled:
        if not oidc_data.oidc_issuer or oidc_data.oidc_issuer.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OIDC Issuer URL is required when OIDC is enabled",
            )
        if not oidc_data.oidc_client_id or oidc_data.oidc_client_id.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OIDC Client ID is required when OIDC is enabled",
            )

    instance_config_service.set_oidc_config(
        db=db,
        oidc_enabled=oidc_data.oidc_enabled,
        oidc_client_id=oidc_data.oidc_client_id,
        oidc_client_secret=oidc_data.oidc_client_secret,
        oidc_issuer=oidc_data.oidc_issuer,
        oidc_redirect_uri=oidc_data.oidc_redirect_uri,
        oidc_provider_name=oidc_data.oidc_provider_name,
        oidc_roles_claim=oidc_data.oidc_roles_claim,
        oidc_admin_group=oidc_data.oidc_admin_group,
        oidc_manager_group=oidc_data.oidc_manager_group,
        oidc_viewer_group=oidc_data.oidc_viewer_group,
        oidc_default_roles=oidc_data.oidc_default_roles,
    )
    return {"message": "OIDC configuration updated successfully"}
