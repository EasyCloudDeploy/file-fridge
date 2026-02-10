import tempfile
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import ColdStorageLocation, MonitoredPath, OperationType, User
from app.security import hash_password
from app.utils.rate_limiter import check_login_rate_limit


@pytest.fixture(autouse=True)
def disable_rate_limit():
    """Disable rate limiting for tests in this module."""
    app.dependency_overrides[check_login_rate_limit] = lambda: None
    yield
    app.dependency_overrides.pop(check_login_rate_limit, None)


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
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    # 3. Try to list root directory
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/browser/list?path=/", headers=headers)

    # 4. Assertions
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Permission denied" in response.json()["detail"]


def test_path_traversal_attempt_with_dots(client: TestClient, db_session: Session):
    """
    Test that a viewer cannot use '..' to traverse out of an allowed path.
    """
    username = "viewer_dots"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)

    with tempfile.TemporaryDirectory() as temp_dir:
        monitored_path = MonitoredPath(
            name="Test Path", source_path=temp_dir, operation_type=OperationType.MOVE, enabled=True
        )
        db_session.add(monitored_path)
        db_session.commit()

        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login_response.status_code == status.HTTP_200_OK
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Attempt to access /tmp/../../etc (or similar)
        target_path = Path(temp_dir).joinpath("..", "..", "etc").resolve()

        response = client.get(f"/api/v1/browser/list?path={target_path}", headers=headers)

        assert response.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}
        if response.status_code == status.HTTP_403_FORBIDDEN:
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
        assert login_response.status_code == status.HTTP_200_OK
        token = login_response.json()["access_token"]

        # 4. Try to list the allowed directory
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get(f"/api/v1/browser/list?path={temp_dir}", headers=headers)

        # 5. Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert any(item["name"] == "test.txt" for item in data["items"])


def test_access_cold_storage_location(client: TestClient, db_session: Session):
    """
    Test that a viewer can access a ColdStorageLocation.
    """
    username = "viewer_cold"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)

    with tempfile.TemporaryDirectory() as temp_dir:
        Path(temp_dir).joinpath("cold.txt").touch()

        cold_loc = ColdStorageLocation(name="Cold Storage", path=temp_dir, is_encrypted=False)
        db_session.add(cold_loc)
        db_session.commit()

        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login_response.status_code == status.HTTP_200_OK
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = client.get(f"/api/v1/browser/list?path={temp_dir}", headers=headers)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert any(item["name"] == "cold.txt" for item in data["items"])


def test_access_subdirectory_monitored_path(client: TestClient, db_session: Session):
    """
    Test that a viewer can access a subdirectory of a MonitoredPath.
    """
    username = "viewer_subdir"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)

    with tempfile.TemporaryDirectory() as temp_dir:
        sub_dir = Path(temp_dir) / "subdir"
        sub_dir.mkdir()
        (sub_dir / "subfile.txt").touch()

        monitored_path = MonitoredPath(
            name="Test Path", source_path=temp_dir, operation_type=OperationType.MOVE, enabled=True
        )
        db_session.add(monitored_path)
        db_session.commit()

        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login_response.status_code == status.HTTP_200_OK
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = client.get(f"/api/v1/browser/list?path={sub_dir}", headers=headers)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert any(item["name"] == "subfile.txt" for item in data["items"])


def test_access_sibling_directory_denied(client: TestClient, db_session: Session):
    """
    Test that a viewer is denied access to a sibling directory of a monitored path.
    """
    username = "viewer_sibling"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)

    with tempfile.TemporaryDirectory() as parent_dir:
        # Create two directories: allowed and forbidden
        allowed_dir = Path(parent_dir) / "allowed"
        allowed_dir.mkdir()
        forbidden_dir = Path(parent_dir) / "forbidden"
        forbidden_dir.mkdir()

        monitored_path = MonitoredPath(
            name="Allowed Path",
            source_path=str(allowed_dir),
            operation_type=OperationType.MOVE,
            enabled=True,
        )
        db_session.add(monitored_path)
        db_session.commit()

        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login_response.status_code == status.HTTP_200_OK
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Verify access to allowed
        resp_allowed = client.get(f"/api/v1/browser/list?path={allowed_dir}", headers=headers)
        assert resp_allowed.status_code == status.HTTP_200_OK

        # Verify denial to forbidden
        resp_forbidden = client.get(f"/api/v1/browser/list?path={forbidden_dir}", headers=headers)
        assert resp_forbidden.status_code == status.HTTP_403_FORBIDDEN


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
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    # 3. Try to list root directory
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/browser/list?path=/", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) > 0
