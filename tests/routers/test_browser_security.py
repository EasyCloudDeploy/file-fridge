
import pytest
from pathlib import Path
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models import User, MonitoredPath, ColdStorageLocation
from app.security import hash_password

def test_browser_path_traversal_protection(client: TestClient, db_session: Session):
    """
    Test that the browser endpoint protects against path traversal.
    """
    # 1. Create a viewer user (non-admin)
    username = "viewer_user"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["viewer"])
    db_session.add(user)

    # 2. Create an allowed path
    allowed_path = "/tmp/allowed"
    monitored_path = MonitoredPath(name="Allowed Path", source_path=allowed_path)
    db_session.add(monitored_path)
    db_session.commit()

    # 3. Login as viewer
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 4. Attempt to browse root (Unauthorized)
    response = client.get("/api/v1/browser/list?path=/", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Access denied" in response.json()["detail"]

    # 5. Attempt to browse allowed path (Authorized)
    # We need the path to exist for the endpoint to not return 404/400
    Path(allowed_path).mkdir(parents=True, exist_ok=True)
    try:
        response = client.get(f"/api/v1/browser/list?path={allowed_path}", headers=headers)
        assert response.status_code == status.HTTP_200_OK
    finally:
        # Cleanup is handled by tmp_path usually but we used /tmp explicitly
        # In a real test we'd use tmp_path fixture but db stores string path.
        pass

def test_browser_admin_bypass(client: TestClient, db_session: Session):
    """
    Test that admins can browse anywhere.
    """
    username = "admin_user"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to browse root (Authorized because admin)
    response = client.get("/api/v1/browser/list?path=/", headers=headers)
    assert response.status_code == status.HTTP_200_OK
