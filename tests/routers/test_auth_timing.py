import pytest
from app.security import authenticate_user, hash_password
from app.models import User

def test_authenticate_user_timing_logic(db_session):
    """
    Test that authenticate_user handles non-existent users correctly.
    This ensures the timing mitigation logic (if present) doesn't break functionality.
    """
    # Create a real user
    username = "timing_test_user"
    password = "correct_password"
    db_session.add(User(username=username, password_hash=hash_password(password)))
    db_session.commit()

    # Case 1: User exists, wrong password
    user = authenticate_user(db_session, username, "wrong_password")
    assert user is None

    # Case 2: User does not exist
    user = authenticate_user(db_session, "non_existent_user", "any_password")
    assert user is None

    # Case 3: User exists, correct password
    user = authenticate_user(db_session, username, password)
    assert user is not None
    assert user.username == username
