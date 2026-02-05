
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models import User
from app.security import hash_password

@pytest.fixture
def viewer_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client with viewer role."""
    username = "viewertestuser"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["viewer"])
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

def test_viewer_cannot_browse_root(viewer_client: TestClient):
    """Test that a viewer cannot browse the root directory."""
    # Attempt to browse root
    response = viewer_client.get("/api/v1/browser/list?path=/")

    # This should fail with 403 Forbidden, but currently returns 200 OK
    assert response.status_code == 403, "Viewer should not be able to browse root directory"

def test_viewer_can_browse_allowed_path(viewer_client: TestClient, db_session: Session, tmp_path):
    """Test that a viewer CAN browse an allowed path."""
    # Create a monitored path
    from app.models import MonitoredPath

    # Create a real directory
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    # Create MonitoredPath entry
    path = MonitoredPath(
        name="Allowed Path",
        source_path=str(allowed_dir),
    )
    db_session.add(path)
    db_session.commit()

    # Attempt to browse valid path
    response = viewer_client.get(f"/api/v1/browser/list?path={str(allowed_dir)}")

    assert response.status_code == 200
    data = response.json()
    assert data["current_path"] == str(allowed_dir)

def test_admin_can_browse_root(client: TestClient, db_session: Session):
    """Test that an admin CAN browse the root directory."""
    # Login as admin
    username = "admintestuser"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    # Attempt to browse root
    response = client.get("/api/v1/browser/list?path=/")

    assert response.status_code == 200
    data = response.json()
    assert data["current_path"] == "/"
