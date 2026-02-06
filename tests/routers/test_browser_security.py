
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models import User, MonitoredPath, ColdStorageLocation
from app.security import hash_password

@pytest.fixture
def non_admin_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client with non-admin role."""
    username = "viewer_user"
    password = "password"
    # Create user with 'viewer' role
    user = User(username=username, password_hash=hash_password(password), roles=["viewer"])
    db_session.add(user)
    db_session.commit()

    # Login
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

@pytest.fixture
def admin_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client with admin role."""
    username = "admin_user"
    password = "password"
    # Create user with 'admin' role
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    # Login
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

def test_list_directory_path_traversal_denied(non_admin_client: TestClient):
    """Test that a non-admin user cannot list directories outside allowed paths."""
    # Try to access root directory which should be restricted
    response = non_admin_client.get("/api/v1/browser/list?path=/")

    # Expect 403 Forbidden because / is not in any MonitoredPath
    assert response.status_code == 403
    assert "Access denied" in response.json()["detail"]

def test_list_directory_allowed_path(non_admin_client: TestClient, db_session: Session, tmp_path):
    """Test that a non-admin user can list directories within allowed paths."""
    # Create a monitored path
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    (allowed_dir / "test.txt").touch()

    mp = MonitoredPath(
        name="Allowed Path",
        source_path=str(allowed_dir),
        operation_type="move",
        check_interval_seconds=3600,
        enabled=True
    )
    db_session.add(mp)
    db_session.commit()

    # Try to access the allowed path
    response = non_admin_client.get(f"/api/v1/browser/list?path={allowed_dir}")

    assert response.status_code == 200
    data = response.json()
    assert data["current_path"] == str(allowed_dir)
    assert len(data["items"]) >= 1

def test_admin_can_access_anywhere(admin_client: TestClient):
    """Test that an admin user can list any directory."""
    # Try to access root directory
    response = admin_client.get("/api/v1/browser/list?path=/")

    # Expect 200 OK because admin is unrestricted
    assert response.status_code == 200
