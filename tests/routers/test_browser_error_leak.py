from unittest.mock import patch

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.security import hash_password


def test_browser_list_error_leak(client: TestClient, db_session: Session):
    """
    Test that internal error details are not leaked in the response.
    """
    # 1. Create an admin user (to bypass permission checks and reach the vulnerable code)
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
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Mock Path.iterdir to raise an exception with sensitive info
    sensitive_info = "SENSITIVE_INTERNAL_INFO_LEAKED"
    with patch("pathlib.Path.iterdir", side_effect=RuntimeError(sensitive_info)):
        response = client.get("/api/v1/browser/list?path=/", headers=headers)

        # 4. Verify the response
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # The test passes if sensitive info is NOT found
        assert sensitive_info not in response.json()["detail"], "Sensitive info leaked in detail"
        assert response.json()["detail"] == "Error browsing directory: Internal server error"
