# ruff: noqa: B008
"""Authentication API routes."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import schemas
from app.config import settings
from app.database import get_db
from app.models import User
from app.security import (
    authenticate_user,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.get("/check", response_model=schemas.AuthCheckResponse)
def check_auth_status(db: Session = Depends(get_db)):
    """
    Check if initial setup is required.

    Returns whether any users exist in the system.
    Used by the frontend to determine whether to show setup or login form.
    """
    user_count = db.query(func.count(User.id)).scalar()
    setup_required = user_count == 0

    return schemas.AuthCheckResponse(setup_required=setup_required, user_count=user_count)


@router.post("/setup", response_model=schemas.Token, status_code=status.HTTP_201_CREATED)
def setup_first_user(user_data: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    Create the first administrator account.

    This endpoint only works when no users exist in the system.
    Once the first user is created, use /login instead.

    Args:
        user_data: Username and password for the first user
        db: Database session

    Returns:
        JWT access token

    Raises:
        HTTPException: 400 if users already exist or username is taken
    """
    # Check if any users exist
    user_count = db.query(func.count(User.id)).scalar()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup has already been completed. Use /login instead.",
        )

    # Check if username is already taken (shouldn't happen but just in case)
    existing_user = db.query(User).filter(User.username == user_data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{user_data.username}' is already taken",
        )

    # Create first user
    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        is_active=True,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"First user created: {user.username}")
    except Exception:
        db.rollback()
        logger.exception("Failed to create first user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        ) from None

    # Generate and return access token
    access_token = create_access_token(data={"sub": user.username})
    return schemas.Token(access_token=access_token, token_type="bearer")


@router.post("/change-password")
def change_password(
    password_data: schemas.PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Change the current user's password.

    Args:
        password_data: Old and new password
        current_user: Currently authenticated user
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: 400 if old password is incorrect
    """

    # Verify old password
    if not verify_password(password_data.old_password, current_user.password_hash):
        logger.warning(f"Failed password change attempt for user: {current_user.username}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect password",
        )

    # Update password
    current_user.password_hash = hash_password(password_data.new_password)

    try:
        db.commit()
        logger.info(f"Password changed for user: {current_user.username}")
        return {"message": "Password changed successfully"}
    except Exception:
        db.rollback()
        logger.exception("Failed to change password")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password",
        ) from None


@router.post("/login", response_model=schemas.Token)
def login(credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    """
    Authenticate a user and return an access token.

    Args:
        credentials: Username and password
        db: Database session

    Returns:
        JWT access token

    Raises:
        HTTPException: 401 if credentials are invalid
    """
    # Authenticate user
    user = authenticate_user(db, credentials.username, credentials.password)
    if not user:
        logger.warning(f"Failed login attempt for username: {credentials.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generate and return access token
    access_token = create_access_token(data={"sub": user.username})
    logger.info(f"User logged in: {user.username}")
    return schemas.Token(access_token=access_token, token_type="bearer")


@router.post("/tokens", response_model=schemas.Token)
def generate_api_token(
    token_data: schemas.TokenCreate,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a manual API token with custom expiration.

    This endpoint requires authentication.
    Use this to generate tokens for external scripts or API access.

    Args:
        token_data: Token configuration (expiration)
        current_user: Currently authenticated user

    Returns:
        JWT access token

    Note:
        - expires_days = None: Use default expiration (from settings)
        - expires_days = 0: No expiration (token never expires)
        - expires_days > 0: Custom expiration in days
    """
    # Determine expiration
    if token_data.expires_days is None:
        # Use default
        expires_delta = timedelta(days=settings.access_token_expire_days)
        logger.info(
            f"User {current_user.username} generated token with default expiration "
            f"({settings.access_token_expire_days} days)"
        )
    elif token_data.expires_days == 0:
        # No expiration
        expires_delta = timedelta(days=365 * 100)  # 100 years (effectively no expiration)
        logger.warning(f"User {current_user.username} generated token with NO expiration")
    else:
        # Custom expiration
        expires_delta = timedelta(days=token_data.expires_days)
        logger.info(
            f"User {current_user.username} generated token with custom expiration "
            f"({token_data.expires_days} days)"
        )

    # Generate and return access token
    access_token = create_access_token(
        data={"sub": current_user.username}, expires_delta=expires_delta
    )
    return schemas.Token(access_token=access_token, token_type="bearer")
