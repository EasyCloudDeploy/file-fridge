import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from app.models import (
    ServerEncryptionKey,
    Notifier,
    NotifierType,
    InstanceMetadata,
    FileInventory,
    StorageType,
    ColdStorageLocation,
    MonitoredPath
)


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

    def test_auto_re_encrypt_db_settings_on_key_rotation(self, authenticated_client, db_session):
        """Test that database settings are automatically re-encrypted when a new server key is generated."""
        notifier = Notifier(
            name="Test Email Notifier",
            type=NotifierType.EMAIL,
            address="test@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="my_secure_password"
        )
        db_session.add(notifier)
        db_session.commit()

        notifier_id = notifier.id
        initial_encrypted = notifier.smtp_password_encrypted
        assert initial_encrypted is not None

        # Rotate server key
        response = authenticated_client.post("/api/v1/encryption/keys")
        assert response.status_code == 200

        # Query notifier again using the stored ID to avoid DetachedInstanceError
        notifier = db_session.query(Notifier).filter_by(id=notifier_id).first()

        # Check decryption works with the new key automatically
        assert notifier.smtp_password == "my_secure_password"
        # Verify the ciphertext changed (new key encryption)
        assert notifier.smtp_password_encrypted != initial_encrypted

    def test_file_key_rotation_and_fallback_and_migration(self, authenticated_client, db_session, tmp_path):
        """Test rotation of file encryption root key, fallback decryption, and migration completion."""
        from app.services.encryption_service import file_encryption_service
        from app.routers.api.encryption import run_file_key_migration

        # 1. Create and encrypt a test file
        input_file = tmp_path / "test.txt"
        input_file.write_bytes(b"File Encryption Test Content")

        encrypted_file = tmp_path / "test.txt.enc"
        decrypted_file = tmp_path / "test.txt.dec"

        # Force root key initialization and encrypt
        file_encryption_service._get_or_create_root_key(db_session)
        file_encryption_service.encrypt_file(db_session, input_file, encrypted_file)

        assert encrypted_file.exists()
        assert encrypted_file.read_bytes() != input_file.read_bytes()

        metadata = db_session.query(InstanceMetadata).first()
        assert metadata is not None
        metadata_id = metadata.id
        original_encrypted_key = metadata.file_encryption_root_key_encrypted
        assert original_encrypted_key is not None

        # Set up a monitored path and local storage location
        path = MonitoredPath(
            name="Test Monitored Path",
            source_path="/tmp/test_source_path",
        )
        db_session.add(path)
        db_session.commit()

        loc = ColdStorageLocation(name="Local Test", path=str(tmp_path))
        db_session.add(loc)
        db_session.commit()

        # Create the file record in inventory
        file_record = FileInventory(
            path_id=path.id,
            file_path=str(encrypted_file),
            file_size=input_file.stat().st_size,
            file_mtime=datetime.now(timezone.utc),
            is_encrypted=True,
            storage_type=StorageType.COLD,
            cold_storage_location_id=loc.id,
            checksum="dummysha256"
        )
        db_session.add(file_record)
        db_session.commit()

        # 2. Rotate file encryption key (intercepting the background task)
        with patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
            response = authenticated_client.post("/api/v1/encryption/keys/rotate-file-key")
            assert response.status_code == 200
            mock_add_task.assert_called_once()
            bg_func = mock_add_task.call_args[0][0]
            bg_arg = mock_add_task.call_args[0][1]
            assert bg_func == run_file_key_migration
            assert bg_arg == metadata_id

        # Query metadata again
        metadata = db_session.query(InstanceMetadata).filter_by(id=metadata_id).first()
        assert metadata.file_encryption_root_key_encrypted != original_encrypted_key
        assert metadata.previous_file_encryption_root_key_encrypted == original_encrypted_key

        # Check status endpoint
        status_resp = authenticated_client.get("/api/v1/encryption/keys/migration-status")
        assert status_resp.status_code == 200
        assert status_resp.json()["in_progress"] is True

        # 3. Test fallback decryption - file is encrypted with old key, but we decrypt using active (with fallback)
        file_encryption_service.decrypt_file(db_session, encrypted_file, decrypted_file)
        assert decrypted_file.exists()
        assert decrypted_file.read_bytes() == b"File Encryption Test Content"
        decrypted_file.unlink()

        # 4. Run background migration manually
        bg_func(bg_arg)

        # Query metadata again
        metadata = db_session.query(InstanceMetadata).filter_by(id=metadata_id).first()
        assert metadata.previous_file_encryption_root_key_encrypted is None
        assert metadata.file_migration_total is None
        assert metadata.file_migration_progress is None

        # Verify status endpoint reflects completion
        status_resp = authenticated_client.get("/api/v1/encryption/keys/migration-status")
        assert status_resp.status_code == 200
        assert status_resp.json()["in_progress"] is False

        # 5. Verify direct decryption using new key (no fallback needed)
        file_encryption_service.decrypt_file(db_session, encrypted_file, decrypted_file)
        assert decrypted_file.exists()
        assert decrypted_file.read_bytes() == b"File Encryption Test Content"
