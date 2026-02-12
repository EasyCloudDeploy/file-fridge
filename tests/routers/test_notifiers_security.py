
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models import User, Notifier, NotifierType
from app.security import hash_password

@pytest.fixture
def authenticated_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client."""
    username = "authtestuser"
    password = "password"
    # Ensure user has 'manager' or 'admin' role to access notifiers
    # Check if user exists first to avoid uniqueness constraint error if re-run
    user = db_session.query(User).filter_by(username=username).first()
    if not user:
        user = User(username=username, password_hash=hash_password(password), roles=["admin"])
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    # Login to get token
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    if response.status_code != 200:
        # Fallback if login fails (e.g. rate limit) - manual token creation?
        # Ideally rate limit shouldn't trigger in tests if using fresh db
        pass

    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

def test_notifier_password_leak(authenticated_client: TestClient, db_session: Session):
    """
    SECURITY TEST: Verify that smtp_password is NOT returned in the API response.
    """
    # Create a notifier with a password directly in DB
    notifier = Notifier(
        name="Security Test Notifier",
        type=NotifierType.EMAIL,
        address="test@example.com",
        smtp_host="smtp.example.com",
        smtp_sender="sender@example.com",
        # smtp_password setter will be used below
    )
    # The model setter handles encryption if we set the property.
    notifier.smtp_password = "SECRET_PASSWORD_DO_NOT_LEAK"

    db_session.add(notifier)
    db_session.commit()
    db_session.refresh(notifier)

    # Verify it is encrypted in DB (optional sanity check)
    assert notifier.smtp_password_encrypted != "SECRET_PASSWORD_DO_NOT_LEAK"

    # Verify property returns plaintext (internal access)
    assert notifier.smtp_password == "SECRET_PASSWORD_DO_NOT_LEAK"

    # Call API
    response = authenticated_client.get("/api/v1/notifiers")
    assert response.status_code == 200
    data = response.json()

    # Find our notifier
    target = next((n for n in data if n["name"] == "Security Test Notifier"), None)
    assert target is not None

    # CRITICAL SECURITY CHECK
    assert "smtp_password" not in target, "smtp_password should NOT be present in API response"

    # Check specific notifier endpoint
    response = authenticated_client.get(f"/api/v1/notifiers/{notifier.id}")
    assert response.status_code == 200
    data = response.json()
    assert "smtp_password" not in data, "smtp_password should NOT be present in API response"

def test_create_notifier_password_handling(authenticated_client: TestClient):
    """
    Verify we can still create a notifier with a password, but it's not echoed back.
    """
    payload = {
        "name": "New Notifier",
        "type": "email",
        "address": "new@example.com",
        "smtp_host": "smtp.new.com",
        "smtp_sender": "new@example.com",
        "smtp_password": "new_secret_password"
    }

    response = authenticated_client.post("/api/v1/notifiers", json=payload)
    assert response.status_code == 201
    data = response.json()

    # Verify creation success
    assert data["name"] == "New Notifier"

    # Verify password is NOT in response
    assert "smtp_password" not in data
