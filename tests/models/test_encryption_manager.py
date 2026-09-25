"""Coverage for EncryptionManager in app.models."""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.models import EncryptionManager


@pytest.mark.unit
class TestEncryptionManager:
    def test_encrypt_decrypt_roundtrip(self):
        mgr = EncryptionManager.get_instance()
        mgr.reset()
        token = Fernet.generate_key().decode()
        with patch.object(mgr, "_get_cipher") as mock_cipher:
            f = Fernet(token)
            mock_cipher.return_value = f
            encrypted = mgr.encrypt("secret-value")
            assert mgr.decrypt(encrypted) == "secret-value"

    def test_encrypt_empty_string(self):
        mgr = EncryptionManager.get_instance()
        assert mgr.encrypt("") == ""

    def test_decrypt_invalid_token_returns_ciphertext(self):
        mgr = EncryptionManager.get_instance()
        with patch.object(mgr, "_get_cipher") as mock_cipher:
            f = MagicMock()
            f.decrypt.side_effect = InvalidToken()
            mock_cipher.return_value = f
            assert mgr.decrypt("not-a-token") == "not-a-token"

    def test_can_decrypt_with_key(self):
        mgr = EncryptionManager.get_instance()
        key = Fernet.generate_key().decode()
        ciphertext = Fernet(key).encrypt(b"ok").decode()
        assert mgr.can_decrypt_with_key(ciphertext, key) is True
        assert mgr.can_decrypt_with_key(ciphertext, Fernet.generate_key().decode()) is False
        assert mgr.can_decrypt_with_key("", key) is False

    def test_get_cipher_generates_key_when_empty(self, monkeypatch):
        mgr = EncryptionManager.get_instance()
        mgr.reset()

        class FakeResult:
            def fetchall(self):
                return []

        fake_db = MagicMock()
        fake_db.execute.return_value = FakeResult()
        fake_db.commit = MagicMock()
        fake_db.close = MagicMock()
        monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)
        monkeypatch.setattr("app.models.os.path.exists", lambda _p: False)

        cipher = mgr._get_cipher()
        assert cipher is not None
        fake_db.execute.assert_called()
