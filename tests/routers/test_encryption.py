import pytest

from app.models import ServerEncryptionKey


@pytest.mark.unit
class TestEncryptionRouter:
    def test_list_keys_success(self, authenticated_client):
        """Test listing encryption keys."""
        response = authenticated_client.get("/api/v1/encryption/keys")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_generate_key_success(self, authenticated_client):
        """Test generating (rotating) a new key."""
        response = authenticated_client.post("/api/v1/encryption/keys")
        assert response.status_code == 200
        data = response.json()
        assert "fingerprint" in data
        assert "created_at" in data

    def test_delete_key_success(self, authenticated_client, db_session):
        """Test deleting an encryption key."""
        # Must have at least 2 keys
        authenticated_client.post("/api/v1/encryption/keys")
        authenticated_client.post("/api/v1/encryption/keys")
        
        keys = db_session.query(ServerEncryptionKey).all()
        assert len(keys) >= 2
        key_id = keys[0].id
        
        response = authenticated_client.delete(f"/api/v1/encryption/keys/{key_id}")
        assert response.status_code == 204
        assert db_session.get(ServerEncryptionKey, key_id) is None

    def test_delete_last_key_fails(self, authenticated_client, db_session):
        """Test that the last encryption key cannot be deleted."""
        # Ensure only 1 key exists
        db_session.query(ServerEncryptionKey).delete()
        authenticated_client.post("/api/v1/encryption/keys")
        
        keys = db_session.query(ServerEncryptionKey).all()
        assert len(keys) == 1
        key_id = keys[0].id
        
        response = authenticated_client.delete(f"/api/v1/encryption/keys/{key_id}")
        assert response.status_code == 400
        assert "last encryption key" in response.json()["detail"].lower()

    def test_delete_key_not_found(self, authenticated_client):
        """Test deleting non-existent key."""
        response = authenticated_client.delete("/api/v1/encryption/keys/9999")
        assert response.status_code == 404
