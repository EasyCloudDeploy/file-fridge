import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.security import authenticate_user, hash_password

def test_authenticate_user_success(db_session: Session):
    """Test that authenticate_user returns the user for correct credentials."""
    username = "timing_test_user"
    password = "correct_password"
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    authenticated_user = authenticate_user(db_session, username, password)
    assert authenticated_user is not None
    assert authenticated_user.username == username

def test_authenticate_user_wrong_password(db_session: Session):
    """Test that authenticate_user returns None for incorrect password."""
    username = "timing_test_user_wrong_pass"
    password = "correct_password"
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    authenticated_user = authenticate_user(db_session, username, "wrong_password")
    assert authenticated_user is None

def test_authenticate_user_non_existent(db_session: Session):
    """Test that authenticate_user returns None for non-existent user."""
    authenticated_user = authenticate_user(db_session, "non_existent_user", "some_password")
    assert authenticated_user is None
