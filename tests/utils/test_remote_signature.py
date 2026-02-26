import pytest
import time
import base64
from unittest.mock import MagicMock

from app.models import RemoteConnection, TrustStatus, RequestNonce
from app.utils.remote_signature import build_message_to_sign, get_signed_headers, verify_signature_from_components
from app.services.identity_service import identity_service


@pytest.mark.unit
@pytest.mark.asyncio
class TestRemoteSignature:
    async def test_sign_and_verify_success(self, db_session):
        """Test full signing and verification flow."""
        # Setup identity
        identity_service._load_or_create_identity(db_session)
        my_fp = identity_service.get_instance_fingerprint(db_session)
        my_pub = identity_service.get_signing_public_key_str(db_session)
        
        # Setup trusted connection
        conn = RemoteConnection(
            name="Trusted Remote", 
            url="http://remote", 
            remote_fingerprint=my_fp,
            remote_ed25519_public_key=my_pub, 
            trust_status=TrustStatus.TRUSTED
        )
        db_session.add(conn)
        db_session.commit()
        
        method = "POST"
        url = "http://remote/api/v1/remote/receive"
        content = b"chunk-data"
        
        # Client side: sign
        headers = await get_signed_headers(db_session, method, url, content)
        
        # Server side: mock Request and verify
        mock_request = MagicMock()
        mock_request.method = method
        mock_request.url.path = "/api/v1/remote/receive"
        mock_request.url.query = ""
        
        verified_conn = await verify_signature_from_components(
            db_session, 
            headers["X-Fingerprint"], 
            headers["X-Timestamp"],
            headers["X-Signature"], 
            headers["X-Nonce"], 
            mock_request, 
            content
        )
        
        assert verified_conn.id == conn.id

    async def test_verify_failure_old_timestamp(self, db_session):
        """Test verification fails with old timestamp."""
        mock_request = MagicMock()
        old_ts = str(int(time.time()) - 1000)
        
        with pytest.raises(Exception) as exc:
            await verify_signature_from_components(
                db_session, "fp", old_ts, "sig", "nonce", mock_request, b""
            )
        assert "timestamp is too old" in str(exc.value.detail).lower()

    async def test_verify_failure_replay_attack(self, db_session):
        """Test verification fails if nonce was already used."""
        now = int(time.time())
        nonce = "used-nonce"
        fp = "some-fp"
        
        # Inject used nonce
        db_session.add(RequestNonce(fingerprint=fp, nonce=nonce, timestamp=now))
        db_session.commit()
        
        mock_request = MagicMock()
        with pytest.raises(Exception) as exc:
            await verify_signature_from_components(
                db_session, fp, str(now), "sig", nonce, mock_request, b""
            )
        assert "already used" in str(exc.value.detail).lower()

    async def test_verify_failure_untrusted_remote(self, db_session):
        """Test verification fails if remote is not trusted."""
        fp = "untrusted-fp"
        conn = RemoteConnection(
            name="Untrusted", url="u", remote_fingerprint=fp, 
            trust_status=TrustStatus.PENDING
        )
        db_session.add(conn)
        db_session.commit()
        
        now = str(int(time.time()))
        mock_request = MagicMock()
        with pytest.raises(Exception) as exc:
            await verify_signature_from_components(
                db_session, fp, now, "sig", "nonce", mock_request, b""
            )
        assert "not trusted" in str(exc.value.detail).lower()

    async def test_verify_failure_invalid_signature(self, db_session):
        """Test verification fails with invalid signature."""
        identity_service._load_or_create_identity(db_session)
        my_fp = identity_service.get_instance_fingerprint(db_session)
        my_pub = identity_service.get_signing_public_key_str(db_session)
        
        conn = RemoteConnection(
            name="Trusted", url="u", remote_fingerprint=my_fp,
            remote_ed25519_public_key=my_pub, trust_status=TrustStatus.TRUSTED
        )
        db_session.add(conn)
        db_session.commit()
        
        now = str(int(time.time()))
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url.path = "/test"
        mock_request.url.query = ""
        
        # Wrong signature (invalid hex or wrong key)
        with pytest.raises(Exception) as exc:
            await verify_signature_from_components(
                db_session, my_fp, now, "00" * 64, "nonce", mock_request, b"body"
            )
        assert "invalid signature" in str(exc.value.detail).lower()
