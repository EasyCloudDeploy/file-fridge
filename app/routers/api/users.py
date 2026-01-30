"""API routes for user management."""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.database import get_db
from app.models import User
from app.security import PermissionChecker, hash_password
from app.services.security_audit_service import security_audit_service

router = APIRouter(prefix="/api/v1/users", tags=["user-management"])
logger = logging.getLogger(__name__)

# Only admins can access these endpoints
admin_only = [Depends(PermissionChecker("admin"))]


@router.get("", response_model=List[schemas.UserOut], dependencies=admin_only)
def list_users(db: Session = Depends(get_db)):
    """List all users."""
    return db.query(User).all()


@router.post("", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED, dependencies=admin_only)
def create_user(user_data: schemas.UserCreate, db: Session = Depends(get_db), current_user: User = Depends(PermissionChecker("admin"))):
    """Create a new user with default role."""
    existing_user = db.query(User).filter(User.username == user_data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken",
        )

    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        is_active=True,
        roles=["viewer"],  # Default role
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
        
        security_audit_service._log(
            db,
            "USER_CREATED",
            f"User created: {user.username}",
            current_user.username,
            {"username": user.username}
        )
        
        return user
    except Exception:
        db.rollback()
        logger.exception("Failed to create user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        )


@router.put("/{user_id}/roles", response_model=schemas.UserOut, dependencies=admin_only)
def update_user_roles(
    user_id: int, 
    roles: List[str], 
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionChecker("admin"))
):
    """Update a user's roles."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent removing own admin role to avoid lockout
    if user.id == current_user.id and "admin" not in roles:
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove admin role from yourself",
        )

    old_roles = user.roles
    user.roles = roles
    
    try:
        db.commit()
        db.refresh(user)
        
        security_audit_service._log(
            db,
            "ROLE_CHANGED",
            f"Roles updated for {user.username}: {old_roles} -> {roles}",
            current_user.username,
            {"username": user.username, "old_roles": old_roles, "new_roles": roles}
        )
        
        return user
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update roles")


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=admin_only)
def delete_user(
    user_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionChecker("admin"))
):
    """Delete a user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    username = user.username
    try:
        db.delete(user)
        db.commit()
        
        security_audit_service._log(
            db,
            "USER_DELETED",
            f"User deleted: {username}",
            current_user.username,
            {"username": username}
        )
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete user")
