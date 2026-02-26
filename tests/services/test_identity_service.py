import pytest
import base64

from app.services.identity_service import identity_service
from app.models import InstanceMetadata


@pytest.mark.unit
class TestIdentityService:
    def test_load_or_create_identity_success(self, db_session):
        """Test creating and loading instance identity."""
        # Initial creation
        metadata = identity_service._load_or_create_identity(db_session)
        assert metadata.instance_uuid is not None
        assert metadata.ed25519_public_key is not None
        assert metadata.x25519_public_key is not None
        
        # Second call should return existing
        metadata2 = identity_service._load_or_create_identity(db_session)
        assert metadata.id == metadata2.id
        assert metadata.instance_uuid == metadata2.instance_uuid

    def test_regenerate_keys_if_incomplete(self, db_session):
        """Test that keys are regenerated if some are missing."""
        metadata = identity_service._load_or_create_identity(db_session)
        old_pub = metadata.ed25519_public_key
        
        # Clear one key
        metadata.ed25519_public_key = None
        db_session.commit()
        
        metadata2 = identity_service._load_or_create_identity(db_session)
        assert metadata2.ed25519_public_key is not None
        assert metadata2.ed25519_public_key != old_pub

    def test_get_instance_fingerprint(self, db_session):
        """Test fingerprint generation."""
        fp = identity_service.get_instance_fingerprint(db_session)
        assert len(fp) == 64  # SHA256 hex
        
        fp2 = identity_service.get_instance_fingerprint(db_session)
        assert fp == fp2

    def test_sign_and_verify_signature(self, db_session):
        """Test signing a message and verifying it."""
        message = b"Hello, File Fridge!"
        signature = identity_service.sign_message(db_session, message)
        assert len(signature) == 64  # Ed25519 signature size
        
        pub_key = identity_service.get_signing_public_key_str(db_session)
        
        # Verify success
        assert identity_service.verify_signature(pub_key, signature, message) is True
        
        # Verify failure with wrong message
        assert identity_service.verify_signature(pub_key, signature, b"Wrong message") is False
        
        # Verify failure with wrong public key (generate a dummy one)
        wrong_pub = base64.b64encode(b"0" * 32).decode("ascii")
        assert identity_service.verify_signature(wrong_pub, signature, message) is False

    def test_get_kx_keys(self, db_session):
        """Test getting key exchange keys."""
        pub_str = identity_service.get_kx_public_key_str(db_session)
        assert pub_str is not None
        
        priv_key = identity_service.get_kx_private_key(db_session)
        assert priv_key is not None

    def test_export_import_pem(self, db_session):
        """Test exporting and importing keys in PEM format."""
        # Export
        pems = identity_service.export_keys_pem(db_session)
        assert "signing_private_key" in pems
        assert "kx_private_key" in pems
        assert "-----BEGIN PRIVATE KEY-----" in pems["signing_private_key"]
        
        # Capture current public keys
        old_signing_pub = identity_service.get_signing_public_key_str(db_session)
        
        # Import (should overwrite)
        identity_service.import_keys_pem(
            db_session, 
            pems["signing_private_key"], 
            pems["kx_private_key"]
        )
        
        # Verify public key matches what was imported
        new_signing_pub = identity_service.get_signing_public_key_str(db_session)
        assert new_signing_pub == old_signing_pub

    def test_import_invalid_pem(self, db_session):
        """Test importing invalid PEM data."""
        with pytest.raises(ValueError, match="Invalid PEM key format"):
            identity_service.import_keys_pem(db_session, "not-a-key", "not-a-key")
