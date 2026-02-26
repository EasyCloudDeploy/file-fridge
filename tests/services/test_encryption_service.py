import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.models import InstanceMetadata
from app.services.encryption_service import file_encryption_service


@pytest.mark.unit
class TestFileEncryptionService:
    def test_get_or_create_root_key(self, db_session):
        """Test that the root key is created and persisted."""
        # Initial call should create the key
        key1 = file_encryption_service._get_or_create_root_key(db_session)
        assert len(key1) == 32
        
        # Verify it's persisted in DB
        metadata = db_session.query(InstanceMetadata).first()
        assert metadata.file_encryption_root_key_encrypted is not None
        
        # Second call should return the same key
        key2 = file_encryption_service._get_or_create_root_key(db_session)
        assert key1 == key2

    def test_derive_key(self, db_session):
        """Test key derivation with different salts."""
        salt1 = os.urandom(16)
        salt2 = os.urandom(16)
        
        key1 = file_encryption_service._derive_key(db_session, salt1)
        key1_again = file_encryption_service._derive_key(db_session, salt1)
        key2 = file_encryption_service._derive_key(db_session, salt2)
        
        assert len(key1) == 32
        assert key1 == key1_again
        assert key1 != key2

    def test_encrypt_decrypt_file(self, db_session, tmp_path):
        """Test full encryption and decryption cycle."""
        input_file = tmp_path / "test.txt"
        input_file.write_bytes(b"Hello, Encryption World! " * 1000)  # ~25KB
        
        encrypted_file = tmp_path / "test.txt.enc"
        decrypted_file = tmp_path / "test.txt.dec"
        
        # Encrypt
        file_encryption_service.encrypt_file(db_session, input_file, encrypted_file)
        
        assert encrypted_file.exists()
        assert encrypted_file.read_bytes() != input_file.read_bytes()
        assert encrypted_file.stat().st_size > input_file.stat().st_size
        
        # Decrypt
        file_encryption_service.decrypt_file(db_session, encrypted_file, decrypted_file)
        
        assert decrypted_file.exists()
        assert decrypted_file.read_bytes() == input_file.read_bytes()

    def test_decrypt_invalid_file_size(self, db_session, tmp_path):
        """Test decryption of a file that is too small."""
        invalid_file = tmp_path / "invalid.enc"
        invalid_file.write_bytes(b"too short")
        
        decrypted_file = tmp_path / "invalid.dec"
        
        with pytest.raises(ValueError, match="File too small"):
            file_encryption_service.decrypt_file(db_session, invalid_file, decrypted_file)

    def test_decrypt_corrupted_ciphertext(self, db_session, tmp_path):
        """Test that corrupted ciphertext fails decryption (tag mismatch)."""
        input_file = tmp_path / "test.txt"
        input_file.write_bytes(b"Sensitive data")
        
        encrypted_file = tmp_path / "test.txt.enc"
        decrypted_file = tmp_path / "test.txt.dec"
        
        file_encryption_service.encrypt_file(db_session, input_file, encrypted_file)
        
        # Corrupt one byte of ciphertext (after salt[16] and nonce[12])
        data = bytearray(encrypted_file.read_bytes())
        data[30] ^= 0xFF 
        encrypted_file.write_bytes(data)
        
        with pytest.raises(Exception):  # cryptography.exceptions.InvalidTag
            file_encryption_service.decrypt_file(db_session, encrypted_file, decrypted_file)
        
        # Output file should be cleaned up on failure
        assert not decrypted_file.exists()

    def test_decrypt_corrupted_tag(self, db_session, tmp_path):
        """Test that corrupted tag fails decryption."""
        input_file = tmp_path / "test.txt"
        input_file.write_bytes(b"Sensitive data")
        
        encrypted_file = tmp_path / "test.txt.enc"
        decrypted_file = tmp_path / "test.txt.dec"
        
        file_encryption_service.encrypt_file(db_session, input_file, encrypted_file)
        
        # Corrupt the tag (last 16 bytes)
        data = bytearray(encrypted_file.read_bytes())
        data[-1] ^= 0xFF 
        encrypted_file.write_bytes(data)
        
        with pytest.raises(Exception):
            file_encryption_service.decrypt_file(db_session, encrypted_file, decrypted_file)
            
        assert not decrypted_file.exists()

    def test_encrypt_nonexistent_file(self, db_session, tmp_path):
        """Test encryption of a nonexistent file."""
        nonexistent = tmp_path / "nonexistent.txt"
        output = tmp_path / "output.enc"
        
        with pytest.raises(FileNotFoundError):
            file_encryption_service.encrypt_file(db_session, nonexistent, output)
            
        assert not output.exists()
