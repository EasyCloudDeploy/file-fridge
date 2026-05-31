"""API routes for system settings management."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import SMTPSettingsUpdate
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
