import pytest
from app.config import settings

@pytest.mark.unit
class TestSettingsRouter:
    def test_get_config_success(self, authenticated_client):
        """Test getting global configurations."""
        response = authenticated_client.get("/api/v1/settings/config")
        assert response.status_code == 200
        data = response.json()
        assert "smtp" in data
        assert "is_env_configured" in data["smtp"]

    def test_update_smtp_config_success(self, authenticated_client, db_session):
        """Test updating SMTP settings successfully when not locked by environment."""
        # Ensure environment host is None during this test
        original_host = settings.smtp_host
        settings.smtp_host = None
        try:
            payload = {
                "smtp_host": "smtp.test-server.com",
                "smtp_port": 465,
                "smtp_user": "test-user",
                "smtp_password": "test-password",
                "smtp_sender": "test@test-server.com",
                "smtp_use_tls": True
            }
            response = authenticated_client.put("/api/v1/settings/smtp", json=payload)
            assert response.status_code == 200
            assert response.json()["message"] == "SMTP configuration updated successfully"

            # Check that it updated in DB / service
            config_resp = authenticated_client.get("/api/v1/settings/config")
            assert config_resp.status_code == 200
            smtp_data = config_resp.json()["smtp"]
            assert smtp_data["smtp_host"]["value"] == "smtp.test-server.com"
            assert smtp_data["smtp_port"]["value"] == 465
            assert smtp_data["smtp_user"]["value"] == "test-user"
            assert smtp_data["smtp_sender"]["value"] == "test@test-server.com"
            assert smtp_data["smtp_use_tls"]["value"] is True
        finally:
            settings.smtp_host = original_host

    def test_update_smtp_config_locked_by_env(self, authenticated_client, db_session, monkeypatch):
        """Test that updating SMTP settings fails when locked by environment variables."""
        original_host = settings.smtp_host
        settings.smtp_host = "smtp.env-locked.com"
        try:
            payload = {
                "smtp_host": "smtp.another-server.com",
                "smtp_port": 587,
                "smtp_user": "another-user",
                "smtp_password": "another-password",
                "smtp_sender": "another@another-server.com",
                "smtp_use_tls": True
            }
            response = authenticated_client.put("/api/v1/settings/smtp", json=payload)
            assert response.status_code == 400
            assert "configured via environment variables" in response.json()["detail"]
        finally:
            settings.smtp_host = original_host
