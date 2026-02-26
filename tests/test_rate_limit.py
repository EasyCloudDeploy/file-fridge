import os
import pytest
from unittest.mock import patch, MagicMock

# Set SECRET_KEY for testing if not set
if "SECRET_KEY" not in os.environ:
    os.environ["SECRET_KEY"] = "test-secret-key-123"

# Set DATABASE_PATH to memory to avoid touching real DB during import
os.environ["DATABASE_PATH"] = ":memory:"

from fastapi.testclient import TestClient
from app.main import app
from app.utils.rate_limiter import _login_rate_limiter
from app.database import get_db

# Override database dependency to avoid DB connection errors
def mock_get_db():
    return MagicMock()

app.dependency_overrides[get_db] = mock_get_db

client = TestClient(app)

@patch.dict(os.environ, {"DISABLE_RATE_LIMIT": "false"})
@patch("app.routers.api.auth.authenticate_user")
def test_login_rate_limit(mock_auth):
    """Test that login endpoint is rate limited to 5 requests per minute."""
    url = "/api/v1/auth/login"

    # Reset rate limiter state
    _login_rate_limiter.requests.clear()

    # Mock authentication failure (so we get 401 instead of 500)
    mock_auth.return_value = None

    # Send 5 requests (should be allowed)
    for i in range(5):
        response = client.post(url, json={"username": "admin", "password": "wrongpassword"})

        # We expect 401 (Unauthorized) because mock_auth returns None
        # But definitely NOT 429
        assert response.status_code != 429, f"Request {i+1} was rate limited unexpectedly"
        assert response.status_code == 401, f"Request {i+1} unexpected status: {response.status_code}"

    # Send 6th request (should be blocked)
    response = client.post(url, json={"username": "admin", "password": "wrongpassword"})
    assert response.status_code == 429, f"Request 6 should have been blocked (Got {response.status_code})"
    assert "Too many login attempts" in response.json()["detail"]
