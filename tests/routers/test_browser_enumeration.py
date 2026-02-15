import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from pathlib import Path
import tempfile

from app.main import app
from app.models import MonitoredPath, OperationType, User
from app.security import hash_password
from app.utils.rate_limiter import check_login_rate_limit

@pytest.fixture(autouse=True)
def disable_rate_limit():
    """Disable rate limiting for tests in this module."""
    app.dependency_overrides[check_login_rate_limit] = lambda: None
    yield
    app.dependency_overrides.pop(check_login_rate_limit, None)

def test_directory_enumeration_vulnerability(client: TestClient, db_session: Session):
    """
    Test that a viewer cannot distinguish between existing and non-existing files
    outside their allowed scope (prevents Enumeration vulnerability).
    """
    username = "enumerator"
    password = "password"
    user = User(
        username=username, password_hash=hash_password(password), roles=["viewer"], is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a directory structure
        # /tmp/allowed (monitored)
        # /tmp/forbidden (not monitored)

        allowed_dir = Path(temp_dir) / "allowed"
        allowed_dir.mkdir()

        forbidden_dir = Path(temp_dir) / "forbidden"
        forbidden_dir.mkdir()

        # Create a file in forbidden directory
        forbidden_file = forbidden_dir / "secret.txt"
        forbidden_file.touch()

        # Add ONLY allowed_dir to monitored paths
        monitored_path = MonitoredPath(
            name="Allowed Path", source_path=str(allowed_dir), operation_type=OperationType.MOVE, enabled=True
        )
        db_session.add(monitored_path)
        db_session.commit()

        # 1. Access existing forbidden file/directory
        # Expectation: 403 Forbidden (because it exists but not allowed)
        response_exists = client.get(f"/api/v1/browser/list?path={forbidden_dir}", headers=headers)

        # 2. Access non-existing forbidden file/directory
        # Expectation: 404 Not Found (because it doesn't exist)
        non_existent_path = forbidden_dir / "does_not_exist"
        response_not_exists = client.get(f"/api/v1/browser/list?path={non_existent_path}", headers=headers)

        print(f"Existing forbidden path status: {response_exists.status_code}")
        print(f"Non-existing forbidden path status: {response_not_exists.status_code}")

        # Vulnerability check: Both status codes should be 403 Forbidden.
        assert response_exists.status_code == status.HTTP_403_FORBIDDEN
        assert response_not_exists.status_code == status.HTTP_403_FORBIDDEN
