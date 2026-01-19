"""Security utilities for authentication and authorization."""

import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)


def _normalize_password(password: str) -> str:
    """
    Normalize password to handle bcrypt's 72-byte limitation.

    Bcrypt has a maximum password length of 72 bytes. To support longer passwords
    while maintaining security, we pre-hash the password with SHA256 and encode
    as base64.

    This approach:
    - Allows unlimited password length
    - Maintains security (SHA256 is cryptographically secure)
    - Produces consistent-length input for bcrypt (44 chars base64)
    - Returns a string that passlib can handle

    Args:
        password: Plain text password

    Returns:
        Base64-encoded SHA256 hash of the password
    """
    password_hash = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(password_hash).decode("ascii")


# HTTP Bearer token scheme for FastAPI
security = HTTPBearer()


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt with SHA256 pre-hashing.

    To handle bcrypt's 72-byte limitation, we pre-hash passwords with SHA256.
    This allows unlimited password length while maintaining security.

    Args:
        password: Plain text password

    Returns:
        Hashed password (bcrypt hash as string)
    """
    normalized = _normalize_password(password)
    # Generate salt and hash the normalized password
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(normalized.encode("utf-8"), salt)
    # Return as string (bcrypt returns bytes)
    return password_hash.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Hashed password to compare against

    Returns:
        True if password matches, False otherwise
    """
    normalized = _normalize_password(plain_password)
    # bcrypt.checkpw expects bytes for both password and hash
    return bcrypt.checkpw(normalized.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.

    Args:
        data: Data to encode in the token (typically {"sub": username})
        expires_delta: Optional custom expiration time delta

    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()

    # Set expiration
    now = datetime.now(tz=timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.access_token_expire_days)

    to_encode.update({"exp": expire, "iat": now})

    # Encode JWT
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        return None


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)
) -> User:
    """
    Dependency to get the current authenticated user from the request.

    This function extracts the JWT token from the Authorization header,
    verifies it, and returns the corresponding user from the database.

    Args:
        credentials: HTTP Bearer credentials from request
        db: Database session

    Returns:
        User object if authentication successful

    Raises:
        HTTPException: 401 if authentication fails
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Extract token
    token = credentials.credentials

    # Verify token
    payload = verify_token(token)
    if payload is None:
        raise credentials_exception

    # Extract username from token
    username: str = payload.get("sub")
    if username is None:
        raise credentials_exception

    # Get user from database
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
        )

    return user


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """
    Authenticate a user by username and password.

    Args:
        db: Database session
        username: Username
        password: Plain text password

    Returns:
        User object if authentication successful, None otherwise
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user
