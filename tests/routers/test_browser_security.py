import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import MonitoredPath, User
from app.security import create_access_token


@pytest.fixture
def viewer_token(db_session: Session):
    user = User(username="viewer", password_hash="hash", roles=["viewer"])
    db_session.add(user)
    db_session.commit()
    return create_access_token(data={"sub": "viewer"})


@pytest.fixture
def admin_token(db_session: Session):
    user = User(username="admin", password_hash="hash", roles=["admin"])
    db_session.add(user)
    db_session.commit()
    return create_access_token(data={"sub": "admin"})


@pytest.fixture
def viewer_client(client: TestClient, viewer_token: str):
    client.headers = {"Authorization": f"Bearer {viewer_token}"}
    return client


@pytest.fixture
def admin_client(client: TestClient, admin_token: str):
    client.headers = {"Authorization": f"Bearer {admin_token}"}
    return client


def test_admin_can_browse_root(admin_client: TestClient):
    """Admin should be able to browse anywhere."""
    response = admin_client.get("/api/v1/browser/list?path=/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["current_path"] == "/"


def test_viewer_cannot_browse_root(viewer_client: TestClient):
    """Viewer should NOT be able to browse root if it's not monitored."""
    # currently this returns 200, but we want 403
    response = viewer_client.get("/api/v1/browser/list?path=/")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_viewer_can_browse_monitored_path(viewer_client: TestClient, db_session: Session, tmp_path):
    """Viewer should be able to browse a monitored path."""
    # Setup monitored path
    allowed_path = tmp_path / "allowed"
    allowed_path.mkdir()

    mp = MonitoredPath(name="Allowed", source_path=str(allowed_path))
    db_session.add(mp)
    db_session.commit()

    response = viewer_client.get(f"/api/v1/browser/list?path={allowed_path!s}")
    assert response.status_code == status.HTTP_200_OK


def test_viewer_cannot_browse_parent_of_monitored_path(
    viewer_client: TestClient, db_session: Session, tmp_path
):
    """Viewer should NOT be able to browse parent of monitored path."""
    # Setup monitored path
    allowed_path = tmp_path / "allowed"
    allowed_path.mkdir()

    mp = MonitoredPath(name="Allowed", source_path=str(allowed_path))
    db_session.add(mp)
    db_session.commit()

    # Try to access parent
    response = viewer_client.get(f"/api/v1/browser/list?path={tmp_path!s}")
    assert response.status_code == status.HTTP_403_FORBIDDEN
