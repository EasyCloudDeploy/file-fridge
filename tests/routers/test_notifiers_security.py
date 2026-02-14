
import os
import sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import User
from app.security import hash_password
from app.config import settings

# Set required environment variables for testing
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_PATH"] = ":memory:"

# Setup in-memory DB
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

@pytest.fixture(scope="module")
def client():
    # Setup override
    app.dependency_overrides[get_db] = override_get_db

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Create admin user
    db = TestingSessionLocal()
    user = User(
        username="admin",
        password_hash=hash_password("admin123"),
        is_active=True,
        roles=["admin"]
    )
    db.add(user)
    db.commit()
    db.close()

    with TestClient(app) as c:
        yield c

    # Teardown
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)

def test_create_notifier_ssrf_prevention(client):
    # Login
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to create insecure webhook
    insecure_notifier = {
        "name": "Insecure Webhook",
        "type": "generic_webhook",
        "address": "http://localhost:8080/internal",
        "enabled": True,
        "subscribed_events": ["SCAN_COMPLETED"]
    }
    response = client.post("/api/v1/notifiers", json=insecure_notifier, headers=headers)

    # Expect 422 Unprocessable Entity (Schema Validation)
    assert response.status_code == 422
    assert "Webhook URLs must use HTTPS" in response.text

def test_notifier_ssrf_prevention(client):
    # Login (reuse token if possible, but simple login again is robust)
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a valid notifier (HTTPS)
    valid_notifier = {
        "name": "Test Webhook",
        "type": "generic_webhook",
        "address": "https://example.com/webhook",
        "enabled": True,
        "subscribed_events": ["SCAN_COMPLETED"]
    }
    response = client.post("/api/v1/notifiers", json=valid_notifier, headers=headers)
    assert response.status_code == 201
    notifier_id = response.json()["id"]

    # Attempt to update to HTTP (SSRF Attempt)
    update_data = {
        "address": "http://localhost:8080/internal-service"
    }
    response = client.put(f"/api/v1/notifiers/{notifier_id}", json=update_data, headers=headers)

    # Expect 400 Bad Request
    assert response.status_code == 400
    assert "Webhook URLs must use HTTPS" in response.json()["detail"]

    # Verify that the address was NOT updated
    response = client.get(f"/api/v1/notifiers/{notifier_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["address"] == "https://example.com/webhook"

def test_notifier_type_change_validation(client):
    # Login
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a valid EMAIL notifier
    email_notifier = {
        "name": "Test Email",
        "type": "email",
        "address": "user@example.com",
        "enabled": True,
        "subscribed_events": ["SCAN_COMPLETED"],
        "smtp_host": "smtp.example.com",
        "smtp_sender": "sender@example.com"
    }
    response = client.post("/api/v1/notifiers", json=email_notifier, headers=headers)
    assert response.status_code == 201
    notifier_id = response.json()["id"]

    # Update type to WEBHOOK (without changing address)
    # This should FAIL because "user@example.com" is not a valid HTTPS URL
    update_data = {
        "type": "generic_webhook"
    }
    response = client.put(f"/api/v1/notifiers/{notifier_id}", json=update_data, headers=headers)

    print(f"Type change response: {response.status_code}")
    print(f"Type change body: {response.json()}")

    assert response.status_code == 400
    detail = response.json()["detail"]
    # It might fail because it's not a URL, or because it's not HTTPS
    assert "Invalid webhook URL" in detail or "Webhook URLs must use HTTPS" in detail

if __name__ == "__main__":
    pass
