import pytest

from app.models import RemoteConnection


@pytest.mark.unit
class TestIdentityRouter:
    def test_export_public_keys_success(self, authenticated_client):
        """Test exporting public keys."""
        response = authenticated_client.get("/api/v1/identity/public-export")
        assert response.status_code == 200
        data = response.json()
        assert "signing_public_key" in data
        assert "kx_public_key" in data
        assert "-----BEGIN PUBLIC KEY-----" in data["signing_public_key"]

    def test_export_private_keys_success(self, authenticated_client):
        """Test exporting private keys with password verification."""
        # password is "password" for the user created in authenticated_client fixture
        payload = {"password": "password"}
        response = authenticated_client.post("/api/v1/identity/private-export", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "signing_private_key" in data
        assert "kx_private_key" in data
        assert "-----BEGIN PRIVATE KEY-----" in data["signing_private_key"]

    def test_export_private_keys_wrong_password(self, authenticated_client):
        """Test that private key export fails with wrong password."""
        payload = {"password": "wrong-password"}
        response = authenticated_client.post("/api/v1/identity/private-export", json=payload)
        assert response.status_code == 401

    def test_import_identity_success(self, authenticated_client, db_session):
        """Test importing identity keys."""
        # First export current keys to get valid PEMs
        payload = {"password": "password"}
        keys = authenticated_client.post("/api/v1/identity/private-export", json=payload).json()
        
        # Add a remote connection to test replacement logic
        db_session.add(RemoteConnection(name="Test", url="u", remote_fingerprint="f1"))
        db_session.commit()
        
        import_payload = {
            "password": "password",
            "signing_private_key": keys["signing_private_key"],
            "kx_private_key": keys["kx_private_key"],
            "confirm_replace": True
        }
        response = authenticated_client.post("/api/v1/identity/import", json=import_payload)
        assert response.status_code == 200
        assert "imported successfully" in response.json()["message"].lower()
        
        # Verify remote connections were cleared
        assert db_session.query(RemoteConnection).count() == 0

    def test_import_identity_conflict_no_confirm(self, authenticated_client, db_session):
        """Test that import fails if remote connections exist and confirm_replace is False."""
        payload = {"password": "password"}
        keys = authenticated_client.post("/api/v1/identity/private-export", json=payload).json()
        
        db_session.add(RemoteConnection(name="Test", url="u", remote_fingerprint="f1"))
        db_session.commit()
        
        import_payload = {
            "password": "password",
            "signing_private_key": keys["signing_private_key"],
            "kx_private_key": keys["kx_private_key"],
            "confirm_replace": False
        }
        response = authenticated_client.post("/api/v1/identity/import", json=import_payload)
        assert response.status_code == 409
        assert "confirm_replace=true" in response.json()["detail"].lower()

    def test_import_identity_invalid_pem(self, authenticated_client):
        """Test importing invalid PEM data."""
        import_payload = {
            "password": "password",
            "signing_private_key": "invalid-pem",
            "kx_private_key": "invalid-pem",
            "confirm_replace": True
        }
        response = authenticated_client.post("/api/v1/identity/import", json=import_payload)
        assert response.status_code == 400
        assert "invalid pem key format" in response.json()["detail"].lower()

    def test_import_identity_unexpected_error(self, authenticated_client, monkeypatch):
        """Test handling of unexpected errors during identity import."""
        from app.services.identity_service import identity_service
        def mock_fail(*args, **kwargs):
            raise Exception("Unexpected error")
        monkeypatch.setattr(identity_service, "import_keys_pem", mock_fail)
        
        import_payload = {
            "password": "password",
            "signing_private_key": "k1",
            "kx_private_key": "k2",
            "confirm_replace": True
        }
        response = authenticated_client.post("/api/v1/identity/import", json=import_payload)
        assert response.status_code == 500
        assert "internal server error" in response.json()["detail"].lower()
