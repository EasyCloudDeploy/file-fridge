
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.security import hash_password

def test_check_auth_status_no_users(client: TestClient):
    """Test the /check endpoint when no users exist."""
    response = client.get("/api/v1/auth/check")
    assert response.status_code == 200
    data = response.json()
    assert data["setup_required"] is True
    assert data["user_count"] == 0

def test_check_auth_status_with_users(client: TestClient, db_session: Session):
    """Test the /check endpoint when users exist."""
    db_session.add(User(username="testuser", password_hash="..."))
    db_session.commit()

    response = client.get("/api/v1/auth/check")
    assert response.status_code == 200
    data = response.json()
    assert data["setup_required"] is False
    assert data["user_count"] == 1


def test_setup_first_user(client: TestClient, db_session: Session):
    """Test creating the first user with the /setup endpoint."""
    response = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "password"},
    )
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    user = db_session.query(User).filter_by(username="admin").first()
    assert user is not None
    assert user.roles == ["admin"]

def test_setup_first_user_already_exists(client: TestClient, db_session: Session):
    """Test that /setup fails if a user already exists."""
    db_session.add(User(username="existing_user", password_hash=hash_password("password")))
    db_session.commit()

    response = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "password"},
    )
    assert response.status_code == 400
    assert "Setup has already been completed" in response.json()["detail"]


def test_login_success(client: TestClient, db_session: Session):
    """Test successful login."""
    username = "testuser"
    password = "testpassword"
    db_session.add(User(username=username, password_hash=hash_password(password)))
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_failure_wrong_password(client: TestClient, db_session: Session):
    """Test login failure with an incorrect password."""
    username = "testuser"
    password = "testpassword"
    db_session.add(User(username=username, password_hash=hash_password(password)))
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "wrongpassword"},
    )
    assert response.status_code == 401
    assert "Incorrect username or password" in response.json()["detail"]

def test_login_failure_wrong_username(client: TestClient):
    """Test login failure with a non-existent username."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "nonexistent", "password": "password"},
    )
    assert response.status_code == 401


@pytest.mark.xfail(reason="Rate limiting is hard to test without time manipulation")
def test_login_rate_limit(client: TestClient, db_session: Session):
    """Test that the login endpoint is rate-limited."""
    username = "testuser"
    password = "testpassword"
    db_session.add(User(username=username, password_hash=hash_password(password)))
    db_session.commit()

    for i in range(5):
        client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})

    response = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    assert response.status_code == 429
    assert "Too many requests" in response.json()["detail"]


def test_change_password_success(authenticated_client: TestClient):
    """Test successful password change."""
    response = authenticated_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "password", "new_password": "newpassword"},
    )
    assert response.status_code == 200
    assert "Password changed successfully" in response.json()["message"]


def test_change_password_wrong_old_password(authenticated_client: TestClient):
    """Test password change with incorrect old password."""
    response = authenticated_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "wrongpassword", "new_password": "newpassword"},
    )
    assert response.status_code == 400
    assert "Incorrect password" in response.json()["detail"]


def test_generate_api_token_default_expiration(authenticated_client: TestClient):
    """Test generating an API token with default expiration."""
    response = authenticated_client.post(
        "/api/v1/auth/tokens",
        json={"expires_days": None},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_generate_api_token_custom_expiration(authenticated_client: TestClient):
    """Test generating an API token with custom expiration."""
    response = authenticated_client.post(
        "/api/v1/auth/tokens",
        json={"expires_days": 7},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data

def test_generate_api_token_no_expiration(authenticated_client: TestClient):
    """Test generating an API token with no expiration."""
    response = authenticated_client.post(
        "/api/v1/auth/tokens",
        json={"expires_days": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
