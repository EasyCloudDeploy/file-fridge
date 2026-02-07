
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User, MonitoredPath
from app.security import hash_password

@pytest.fixture
def viewer_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client with viewer role."""
    username = "viewer_user"
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

def test_path_traversal_viewer_restricted(viewer_client: TestClient):
    """
    Test that a user with 'viewer' role CANNOT browse arbitrary paths.
    """
    # Try to list the current directory (not in allowed paths)
    response = viewer_client.get("/api/v1/browser/list?path=.")
    assert response.status_code == 403

    # Try to traverse up
    response = viewer_client.get("/api/v1/browser/list?path=..")
    assert response.status_code == 403

    # Try absolute path
    response = viewer_client.get("/api/v1/browser/list?path=/")
    assert response.status_code == 403

def test_path_traversal_viewer_allowed(viewer_client: TestClient, db_session: Session, tmp_path):
    """
    Test that a user with 'viewer' role CAN browse allowed paths.
    """
    # Create a monitored path
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    (allowed_dir / "test.txt").touch()

    mp = MonitoredPath(
        name="Test Path",
        source_path=str(allowed_dir),
        check_interval_seconds=3600
    )
    db_session.add(mp)
    db_session.commit()

    # Browse the allowed path
    response = viewer_client.get(f"/api/v1/browser/list?path={allowed_dir}")
    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 1
    assert data["items"][0]["name"] == "test.txt"

    # Browse sub-path of allowed path
    subdir = allowed_dir / "subdir"
    subdir.mkdir()
    response = viewer_client.get(f"/api/v1/browser/list?path={subdir}")
    assert response.status_code == 200
