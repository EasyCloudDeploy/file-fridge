import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import MonitoredPath, OperationType, User
from app.security import create_access_token, hash_password


# Fixture for admin user
@pytest.fixture
def admin_client(client: TestClient, db_session: Session):
    username = "admin"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    token = create_access_token(data={"sub": username, "roles": ["admin"]})
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# Fixture for viewer user
@pytest.fixture
def viewer_client(client: TestClient, db_session: Session):
    username = "viewer"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["viewer"])
    db_session.add(user)
    db_session.commit()

    token = create_access_token(data={"sub": username, "roles": ["viewer"]})
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def test_admin_can_browse_root(admin_client: TestClient):
    """Admin should be able to browse /"""
    response = admin_client.get("/api/v1/browser/list?path=/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["current_path"] == "/"


def test_viewer_cannot_browse_root(viewer_client: TestClient):
    """Viewer should NOT be able to browse / (unless it's a monitored path, which is unlikely in tests)"""
    response = viewer_client.get("/api/v1/browser/list?path=/")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Access denied" in response.json()["detail"]


def test_viewer_can_browse_allowed_path(viewer_client: TestClient, db_session: Session, tmp_path):
    """Viewer should be able to browse a monitored path"""
    # Create a monitored path
    allowed_path = tmp_path / "allowed"
    allowed_path.mkdir()

    # Add some files
    (allowed_path / "file1.txt").write_text("content")

    monitored_path = MonitoredPath(
        name="Test Path", source_path=str(allowed_path), operation_type=OperationType.MOVE
    )
    db_session.add(monitored_path)
    db_session.commit()

    # Try to browse it
    response = viewer_client.get(f"/api/v1/browser/list?path={allowed_path!s}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["current_path"] == str(allowed_path)
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "file1.txt"


def test_viewer_can_browse_subdirectory_of_allowed_path(
    viewer_client: TestClient, db_session: Session, tmp_path
):
    """Viewer should be able to browse a subdirectory of a monitored path"""
    # Create a monitored path
    allowed_path = tmp_path / "allowed"
    subdir = allowed_path / "subdir"
    subdir.mkdir(parents=True)

    monitored_path = MonitoredPath(
        name="Test Path", source_path=str(allowed_path), operation_type=OperationType.MOVE
    )
    db_session.add(monitored_path)
    db_session.commit()

    # Try to browse subdirectory
    response = viewer_client.get(f"/api/v1/browser/list?path={subdir!s}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["current_path"] == str(subdir)


def test_viewer_cannot_browse_parent_of_allowed_path(
    viewer_client: TestClient, db_session: Session, tmp_path
):
    """Viewer should NOT be able to browse parent of a monitored path via traversal"""
    # Create a monitored path
    allowed_path = tmp_path / "allowed"
    allowed_path.mkdir()

    monitored_path = MonitoredPath(
        name="Test Path", source_path=str(allowed_path), operation_type=OperationType.MOVE
    )
    db_session.add(monitored_path)
    db_session.commit()

    # Try to browse parent
    parent_path = allowed_path.parent
    response = viewer_client.get(f"/api/v1/browser/list?path={parent_path!s}")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_viewer_cannot_use_dotdot_to_escape(
    viewer_client: TestClient, db_session: Session, tmp_path
):
    """Viewer should NOT be able to use .. to escape"""
    # Create a monitored path
    allowed_path = tmp_path / "allowed"
    allowed_path.mkdir()

    monitored_path = MonitoredPath(
        name="Test Path", source_path=str(allowed_path), operation_type=OperationType.MOVE
    )
    db_session.add(monitored_path)
    db_session.commit()

    # Try to use ..
    # Note: The server resolves the path before checking, so /allowed/.. becomes /parent
    # We pass the raw path string
    path_with_traversal = f"{allowed_path!s}/.."
    response = viewer_client.get(f"/api/v1/browser/list?path={path_with_traversal}")
    assert response.status_code == status.HTTP_403_FORBIDDEN
