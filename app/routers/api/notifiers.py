# ruff: noqa: B008
"""API endpoints for notifier management."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import EmailStr, HttpUrl, TypeAdapter
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notifier as NotifierModel
from app.models import NotifierType
from app.schemas import (
    Notifier,
    NotifierCreate,
    NotifierUpdate,
    TestNotifierResponse,
)
from app.services.notification_service import notification_service
from app.utils.sanitization import sanitize_for_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notifiers", tags=["notifiers"])


def _validate_notifier_config(
    notifier_id: Optional[int], address: str, notifier_type: NotifierType
) -> None:
    """
    Validate notifier configuration to prevent security issues.

    Ensures:
    - Emails are valid email addresses.
    - Webhooks use HTTPS to prevent SSRF.
    """
    context = f"notifier {notifier_id}" if notifier_id else "new notifier"

    if notifier_type == NotifierType.EMAIL:
        try:
            TypeAdapter(EmailStr).validate_python(address)
        except Exception as e:
            logger.warning(
                f"Invalid email address provided for {context}: {sanitize_for_log(str(e))}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email address format",
            ) from e

    elif notifier_type == NotifierType.GENERIC_WEBHOOK:
        try:
            # Use TypeAdapter(HttpUrl) for Pydantic V2 compatibility
            url = TypeAdapter(HttpUrl).validate_python(address)
            if url.scheme != "https":
                logger.warning(f"Insecure webhook URL provided for {context}: scheme={url.scheme}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Webhook URLs must use HTTPS for security",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(
                f"Invalid webhook URL provided for {context}: {sanitize_for_log(str(e))}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid webhook URL format",
            ) from e


@router.get("", response_model=List[Notifier])
def list_notifiers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    List all configured notifiers.

    Args:
        skip: Number of records to skip (for pagination)
        limit: Maximum number of records to return
        db: Database session

    Returns:
        List of notifiers
    """
    return db.query(NotifierModel).offset(skip).limit(limit).all()


@router.get("/{notifier_id}", response_model=Notifier)
def get_notifier(notifier_id: int, db: Session = Depends(get_db)):
    """
    Get a specific notifier by ID.

    Args:
        notifier_id: Notifier ID
        db: Database session

    Returns:
        Notifier object

    Raises:
        HTTPException: If notifier not found
    """
    notifier = db.query(NotifierModel).filter(NotifierModel.id == notifier_id).first()
    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notifier with ID {notifier_id} not found",
        )
    return notifier


@router.post("", response_model=Notifier, status_code=status.HTTP_201_CREATED)
def create_notifier(notifier: NotifierCreate, db: Session = Depends(get_db)):
    """
    Create a new notifier.

    Args:
        notifier: Notifier data
        db: Database session

    Returns:
        Created notifier object
    """
    # Check for duplicate name
    existing = db.query(NotifierModel).filter(NotifierModel.name == notifier.name).first()
    if existing:
        # Sanitize error message: Do not reflect user input 'name'
        logger.info(f"Attempt to create duplicate notifier: {sanitize_for_log(notifier.name)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notifier with this name already exists",
        )

    # Validate configuration (SSRF Prevention)
    # Note: Pydantic schema validation happens first, but we double-check here for consistency
    # and to ensure HTTPS enforcement is robust.
    _validate_notifier_config(None, notifier.address, notifier.type)

    # Create notifier (password will be encrypted via property setter)
    db_notifier = NotifierModel(
        name=notifier.name,
        type=notifier.type,
        address=notifier.address,
        enabled=notifier.enabled,
        subscribed_events=notifier.subscribed_events,
        smtp_host=notifier.smtp_host,
        smtp_port=notifier.smtp_port,
        smtp_user=notifier.smtp_user,
        smtp_password=notifier.smtp_password,  # Property setter handles encryption
        smtp_sender=notifier.smtp_sender,
        smtp_use_tls=notifier.smtp_use_tls,
    )
    db.add(db_notifier)
    db.commit()
    db.refresh(db_notifier)
    logger.info(f"Created notifier '{sanitize_for_log(notifier.name)}' ({notifier.type})")
    return db_notifier


@router.put("/{notifier_id}", response_model=Notifier)
def update_notifier(
    notifier_id: int, notifier_update: NotifierUpdate, db: Session = Depends(get_db)
):
    """
    Update an existing notifier.

    Args:
        notifier_id: Notifier ID
        notifier_update: Updated notifier data
        db: Database session

    Returns:
        Updated notifier object

    Raises:
        HTTPException: If notifier not found
    """
    db_notifier = db.query(NotifierModel).filter(NotifierModel.id == notifier_id).first()
    if not db_notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notifier with ID {notifier_id} not found",
        )

    # Check for duplicate name (if name is being updated)
    if notifier_update.name and notifier_update.name != db_notifier.name:
        existing = (
            db.query(NotifierModel)
            .filter(NotifierModel.name == notifier_update.name, NotifierModel.id != notifier_id)
            .first()
        )
        if existing:
            # Sanitize error message: Do not reflect user input 'name'
            logger.info(
                f"Attempt to update to duplicate notifier name: {sanitize_for_log(notifier_update.name)}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Notifier with this name already exists",
            )

    # Update only provided fields
    update_data = notifier_update.model_dump(exclude_unset=True)

    # Security Validation: Validate address if address OR type is being updated
    # This prevents invalid state like type=WEBHOOK but address=email (if only type is updated)
    if "address" in update_data or "type" in update_data:
        new_address = update_data.get("address", db_notifier.address)
        new_type = update_data.get("type", db_notifier.type)
        _validate_notifier_config(notifier_id, new_address, new_type)

    for field, value in update_data.items():
        if field == "smtp_password" and value is not None:
            # Use property setter to encrypt password
            db_notifier.smtp_password = value
        else:
            setattr(db_notifier, field, value)

    db.commit()
    db.refresh(db_notifier)
    logger.info(f"Updated notifier '{sanitize_for_log(db_notifier.name)}'")
    return db_notifier


@router.delete("/{notifier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notifier(notifier_id: int, db: Session = Depends(get_db)):
    """
    Delete a notifier.

    Args:
        notifier_id: Notifier ID
        db: Database session

    Raises:
        HTTPException: If notifier not found
    """
    db_notifier = db.query(NotifierModel).filter(NotifierModel.id == notifier_id).first()
    if not db_notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notifier with ID {notifier_id} not found",
        )

    notifier_name = db_notifier.name
    db.delete(db_notifier)
    db.commit()
    logger.info(f"Deleted notifier '{sanitize_for_log(notifier_name)}'")


@router.post("/{notifier_id}/test", response_model=TestNotifierResponse)
async def test_notifier(notifier_id: int, db: Session = Depends(get_db)):
    """
    Send a test notification to a specific notifier.

    This endpoint is crucial for validating notifier configuration.

    Args:
        notifier_id: Notifier ID
        db: Database session

    Returns:
        Test result with success status and message

    Raises:
        HTTPException: If notifier not found
    """
    # Check if notifier exists
    notifier = db.query(NotifierModel).filter(NotifierModel.id == notifier_id).first()
    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notifier with ID {notifier_id} not found",
        )

    # Send test notification
    success, message = await notification_service.test_notifier(db, notifier_id)

    return TestNotifierResponse(
        success=success, message=message, notifier_name=sanitize_for_log(notifier.name)
    )
