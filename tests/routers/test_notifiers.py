import pytest
from unittest.mock import AsyncMock

from app.models import Notifier, NotifierType


@pytest.mark.unit
class TestNotifiersRouter:
    def test_list_notifiers_success(self, authenticated_client, db_session):
        """Test listing configured notifiers."""
        n = Notifier(name="Notifier 1", type=NotifierType.EMAIL, address="test1@example.com")
        db_session.add(n)
        db_session.commit()
        
        response = authenticated_client.get("/api/v1/notifiers")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(notif["name"] == "Notifier 1" for notif in data)

    def test_get_notifier_success(self, authenticated_client, db_session):
        """Test getting a specific notifier."""
        n = Notifier(name="Notifier 2", type=NotifierType.EMAIL, address="test2@example.com")
        db_session.add(n)
        db_session.commit()
        
        response = authenticated_client.get(f"/api/v1/notifiers/{n.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Notifier 2"

    def test_get_notifier_not_found(self, authenticated_client):
        """Test getting non-existent notifier."""
        response = authenticated_client.get("/api/v1/notifiers/9999")
        assert response.status_code == 404

    def test_create_email_notifier_success(self, authenticated_client):
        """Test creating a valid email notifier."""
        payload = {
            "name": "Alert Email",
            "type": "email",
            "address": "alerts@company.com",
            "subscribed_events": ["SCAN_COMPLETED", "SCAN_ERROR"],
            "smtp_host": "smtp.example.com",
            "smtp_sender": "fridge@example.com",
            "enabled": True
        }
        response = authenticated_client.post("/api/v1/notifiers", json=payload)
        assert response.status_code == 201
        assert response.json()["name"] == "Alert Email"
        assert response.json()["address"] == "alerts@company.com"

    def test_create_webhook_notifier_success(self, authenticated_client):
        """Test creating a valid HTTPS webhook notifier."""
        payload = {
            "name": "Secure Webhook",
            "type": "generic_webhook",
            "address": "https://hooks.slack.com/services/xxx",
            "subscribed_events": ["DISK_SPACE_CRITICAL"]
        }
        response = authenticated_client.post("/api/v1/notifiers", json=payload)
        assert response.status_code == 201
        assert response.json()["type"] == "generic_webhook"

    def test_create_webhook_insecure_fails(self, authenticated_client):
        """Test that insecure HTTP webhooks are rejected."""
        payload = {
            "name": "Insecure Webhook",
            "type": "generic_webhook",
            "address": "http://insecure-site.com/hook",
            "subscribed_events": ["SCAN_ERROR"]
        }
        response = authenticated_client.post("/api/v1/notifiers", json=payload)
        # Pydantic validator in NotifierBase raises ValueError for non-https,
        # which FastAPI returns as 422 Unprocessable Entity.
        assert response.status_code == 422
        assert "https" in str(response.json()["detail"]).lower()

    def test_create_notifier_duplicate_name(self, authenticated_client, db_session):
        """Test preventing duplicate notifier names."""
        n = Notifier(name="Common Name", type=NotifierType.EMAIL, address="c1@ex.com")
        db_session.add(n)
        db_session.commit()
        
        payload = {
            "name": "Common Name",
            "type": "email",
            "address": "c2@ex.com",
            "smtp_host": "smtp.example.com",
            "smtp_sender": "fridge@example.com",
            "subscribed_events": []
        }
        response = authenticated_client.post("/api/v1/notifiers", json=payload)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"].lower()

    def test_update_notifier_success(self, authenticated_client, db_session):
        """Test updating a notifier."""
        n = Notifier(name="Old Notif", type=NotifierType.EMAIL, address="old@ex.com")
        db_session.add(n)
        db_session.commit()
        
        payload = {"name": "New Notif", "address": "new@ex.com"}
        response = authenticated_client.put(f"/api/v1/notifiers/{n.id}", json=payload)
        assert response.status_code == 200
        assert response.json()["name"] == "New Notif"
        assert response.json()["address"] == "new@ex.com"

    def test_delete_notifier_success(self, authenticated_client, db_session):
        """Test deleting a notifier."""
        n = Notifier(name="To Delete", type=NotifierType.EMAIL, address="del@ex.com")
        db_session.add(n)
        db_session.commit()
        notif_id = n.id
        
        response = authenticated_client.delete(f"/api/v1/notifiers/{notif_id}")
        assert response.status_code == 204
        assert db_session.get(Notifier, notif_id) is None

    @pytest.mark.asyncio
    async def test_test_notifier_endpoint(self, authenticated_client, db_session, monkeypatch):
        """Test the notifier test endpoint."""
        n = Notifier(name="Test Me", type=NotifierType.EMAIL, address="me@ex.com")
        db_session.add(n)
        db_session.commit()
        
        from app.services.notification_service import notification_service
        mock_test = AsyncMock(return_value=(True, "Test successful"))
        monkeypatch.setattr(notification_service, "test_notifier", mock_test)
        
        response = authenticated_client.post(f"/api/v1/notifiers/{n.id}/test")
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert "successful" in response.json()["message"].lower()
