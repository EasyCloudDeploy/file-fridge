"""API endpoints for notifier management."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notifier as NotifierModel
from app.schemas import (
    Notifier,
    NotifierCreate,
    NotifierUpdate,
    TestNotifierResponse,
)
from app.services.notification_service import notification_service

router = APIRouter(prefix="/api/v1/notifiers", tags=["notifiers"])


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
    db_notifier = NotifierModel(**notifier.model_dump())
    db.add(db_notifier)
    db.commit()
    db.refresh(db_notifier)
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

    # Update only provided fields
    update_data = notifier_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_notifier, field, value)

    db.commit()
    db.refresh(db_notifier)
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

    db.delete(db_notifier)
    db.commit()


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

    return TestNotifierResponse(success=success, message=message, notifier_name=notifier.name)
