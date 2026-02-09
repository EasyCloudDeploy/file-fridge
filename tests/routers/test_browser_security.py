import tempfile
from pathlib import Path

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import MonitoredPath, OperationType, User
from app.security import hash_password


def test_path_traversal_viewer_forbidden(client: TestClient, db_session: Session):
    """
    Test that a viewer cannot access arbitrary paths like root /.
    """
    # 1. Create a viewer user
    username = "viewer"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # 2. Login
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = login_response.json()["access_token"]

    # 3. Try to list root directory
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/browser/list?path=/", headers=headers)

    # 4. Assertions
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Permission denied" in response.json()["detail"]


def test_viewer_access_allowed_path(client: TestClient, db_session: Session):
    """
    Test that a viewer CAN access a path that is configured as a MonitoredPath.
    """
    # 1. Create a viewer user
    username = "viewer_allowed"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)

    # 2. Create a temporary directory and add it as a MonitoredPath
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a file inside
        Path(temp_dir).joinpath("test.txt").touch()

        monitored_path = MonitoredPath(
            name="Test Path", source_path=temp_dir, operation_type=OperationType.MOVE, enabled=True
        )
        db_session.add(monitored_path)
        db_session.commit()

        # 3. Login
        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        token = login_response.json()["access_token"]

        # 4. Try to list the allowed directory
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get(f"/api/v1/browser/list?path={temp_dir}", headers=headers)

        # 5. Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert any(item["name"] == "test.txt" for item in data["items"])


def test_admin_access_root(client: TestClient, db_session: Session):
    """
    Test that an admin CAN access arbitrary paths (unrestricted).
    """
    # 1. Create an admin user
    username = "admin"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["admin"], is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # 2. Login
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = login_response.json()["access_token"]

    # 3. Try to list root directory
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/browser/list?path=/", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) > 0
