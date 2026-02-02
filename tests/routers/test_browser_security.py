
import pytest
from pathlib import Path
from contextlib import contextmanager
from app.models import MonitoredPath, User
from app.security import get_current_user
from app.main import app

@contextmanager
def override_user_role(role: str):
    """Context manager to override the current user with a specific role."""
    def mock_get_current_user():
        return User(username=f"test_{role}", roles=[role], is_active=True)

    app.dependency_overrides[get_current_user] = mock_get_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)

def test_browser_path_traversal_viewer(client, db_session, tmp_path):
    # Setup directories
    allowed_dir = tmp_path / "allowed"
    forbidden_dir = tmp_path / "forbidden"
    allowed_dir.mkdir()
    forbidden_dir.mkdir()

    # Create a file in forbidden dir to verify we can see it (or not)
    (forbidden_dir / "secret.txt").touch()

    # Add allowed path to DB
    monitored_path = MonitoredPath(
        name="Allowed Path",
        source_path=str(allowed_dir),
        enabled=True
    )
    db_session.add(monitored_path)
    db_session.commit()

    with override_user_role("viewer"):
        # Attempt to browse forbidden directory
        response = client.get(f"/api/v1/browser/list?path={forbidden_dir}")

        # Should be 403 Forbidden
        assert response.status_code == 403
        assert "Access denied" in response.json()['detail']

def test_browser_admin_access(client, db_session, tmp_path):
    # Setup directories
    forbidden_dir = tmp_path / "forbidden"
    forbidden_dir.mkdir(exist_ok=True)

    with override_user_role("admin"):
        # Admin should be able to browse anywhere
        response = client.get(f"/api/v1/browser/list?path={forbidden_dir}")
        assert response.status_code == 200
