import pytest
import respx
import json
import base64
from httpx import Response

from app.models import RemoteConnection, TrustStatus, TransferMode
from app.services.remote_connection_service import remote_connection_service, canonical_json_encode


@pytest.mark.unit
class TestRemoteConnectionService:
    def test_canonical_json_encode(self):
        """Test canonical JSON encoding for consistent signing."""
        data = {"b": 2, "a": 1}
        encoded = canonical_json_encode(data)
        assert encoded == b'{"a":1,"b":2}'

    def test_list_connections(self, db_session):
        """Test listing connections."""
        conn = RemoteConnection(name="Remote 1", url="http://remote1", remote_fingerprint="f1")
        db_session.add(conn)
        db_session.commit()
        
        results = remote_connection_service.list_connections(db_session)
        assert len(results) >= 1
        assert any(c.name == "Remote 1" for c in results)

    def test_get_connection_by_fingerprint(self, db_session):
        """Test finding connection by fingerprint."""
        conn = RemoteConnection(name="Remote 2", url="http://remote2", remote_fingerprint="f2")
        db_session.add(conn)
        db_session.commit()
        
        found = remote_connection_service.get_connection_by_fingerprint(db_session, "f2")
        assert found is not None
        assert found.name == "Remote 2"
        
        not_found = remote_connection_service.get_connection_by_fingerprint(db_session, "missing")
        assert not_found is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_remote_identity_success(self):
        """Test fetching remote identity."""
        # Valid SHA256 hex (64 chars)
        dummy_fp = "a" * 64
        # Valid base64 (32 bytes -> 44 chars)
        dummy_key = base64.b64encode(b"0" * 32).decode("ascii")
        
        respx.get("http://remote/api/v1/remote/identity").mock(return_value=Response(200, json={
            "instance_name": "Remote Instance",
            "fingerprint": dummy_fp,
            "ed25519_public_key": dummy_key,
            "x25519_public_key": dummy_key,
            "url": "http://remote"
        }))
        
        identity = await remote_connection_service.get_remote_identity("http://remote")
        assert identity.instance_name == "Remote Instance"
        assert identity.fingerprint == dummy_fp

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_remote_identity_failure(self):
        """Test error handling when fetching remote identity fails."""
        respx.get("http://remote-fail/api/v1/remote/identity").mock(return_value=Response(500))
        
        with pytest.raises(ValueError, match="Could not fetch identity"):
            await remote_connection_service.get_remote_identity("http://remote-fail")

    def test_trust_reject_connection(self, db_session):
        """Test manually trusting and rejecting connections."""
        conn = RemoteConnection(
            name="Pending", url="url", remote_fingerprint="fp-p", 
            trust_status=TrustStatus.PENDING
        )
        db_session.add(conn)
        db_session.commit()
        
        # Trust
        remote_connection_service.trust_connection(db_session, conn.id)
        assert conn.trust_status == TrustStatus.TRUSTED
        
        # Set back to pending for reject test
        conn.trust_status = TrustStatus.PENDING
        db_session.commit()
        
        # Reject
        remote_connection_service.reject_connection(db_session, conn.id)
        assert conn.trust_status == TrustStatus.REJECTED

    def test_handle_terminate_connection(self, db_session):
        """Test handling incoming termination request."""
        conn = RemoteConnection(
            name="To Terminate", url="url", remote_fingerprint="fp-t", 
            trust_status=TrustStatus.TRUSTED
        )
        db_session.add(conn)
        db_session.commit()
        
        remote_connection_service.handle_terminate_connection(db_session, "fp-t")
        assert conn.trust_status == TrustStatus.REJECTED

    @pytest.mark.asyncio
    @respx.mock
    async def test_initiate_connection_success(self, db_session, monkeypatch):
        """Test full connection initiation flow."""
        # 1. Setup mocks
        from app.services.instance_config_service import instance_config_service
        monkeypatch.setattr(instance_config_service, "get_instance_url", lambda db: "http://local")
        
        dummy_fp = "b" * 64
        dummy_key = base64.b64encode(b"1" * 32).decode("ascii")
        
        from app.schemas import RemoteConnectionIdentity
        remote_id = RemoteConnectionIdentity(
            instance_name="Remote",
            fingerprint=dummy_fp,
            ed25519_public_key=dummy_key,
            x25519_public_key=dummy_key,
            url="http://remote"
        )
        
        # Mock connection request response
        mock_response_payload = {
            "identity": {
                "instance_name": "Remote",
                "fingerprint": dummy_fp,
                "url": "http://remote",
                "transfer_mode": "PUSH_ONLY"
            },
            "signature": "00" * 64 # dummy hex sig
        }
        respx.post("http://remote/api/v1/remote/connection-request").mock(
            return_value=Response(200, json=mock_response_payload)
        )
        
        # Mock signature verification
        from app.services.identity_service import identity_service
        monkeypatch.setattr(identity_service, "verify_signature", lambda pk, sig, msg: True)
        
        # 2. Call initiate
        conn = await remote_connection_service.initiate_connection(
            db_session, "My Remote", remote_id
        )
        
        assert conn.name == "My Remote"
        assert conn.trust_status == TrustStatus.TRUSTED
        assert db_session.query(RemoteConnection).filter_by(remote_fingerprint=dummy_fp).first() is not None

    def test_handle_connection_request_success(self, db_session, monkeypatch):
        """Test handling an incoming connection request."""
        # Setup mocks
        from app.services.instance_config_service import instance_config_service
        monkeypatch.setattr(instance_config_service, "get_instance_url", lambda db: "http://local")
        
        dummy_fp = "c" * 64
        dummy_key = base64.b64encode(b"2" * 32).decode("ascii")
        
        request_data = {
            "identity": {
                "instance_name": "Remote Sender",
                "fingerprint": dummy_fp,
                "ed25519_public_key": dummy_key,
                "x25519_public_key": dummy_key,
                "url": "http://remote-sender",
                "transfer_mode": "BIDIRECTIONAL"
            },
            "signature": "00" * 64  # dummy
        }
        
        # Mock signature verification
        from app.services.identity_service import identity_service
        monkeypatch.setattr(identity_service, "verify_signature", lambda pk, sig, msg: True)
        
        response = remote_connection_service.handle_connection_request(db_session, request_data)
        
        assert "identity" in response
        assert "signature" in response
        
        # Verify connection created in DB as PENDING
        conn = db_session.query(RemoteConnection).filter_by(remote_fingerprint=dummy_fp).first()
        assert conn is not None
        assert conn.trust_status == TrustStatus.PENDING
        assert conn.remote_transfer_mode == TransferMode.BIDIRECTIONAL

    def test_handle_connection_request_invalid_signature(self, db_session, monkeypatch):
        """Test that connection request fails with invalid signature."""
        from app.services.instance_config_service import instance_config_service
        monkeypatch.setattr(instance_config_service, "get_instance_url", lambda db: "http://local")
        
        from app.services.identity_service import identity_service
        monkeypatch.setattr(identity_service, "verify_signature", lambda pk, sig, msg: False)
        
        request_data = {
            "identity": {
                "instance_name": "Remote", "fingerprint": "f"*64, 
                "ed25519_public_key": base64.b64encode(b"0"*32).decode("ascii"),
                "x25519_public_key": base64.b64encode(b"0"*32).decode("ascii"),
                "url": "http://r"
            },
            "signature": "bad0"
        }
        
        with pytest.raises(ValueError, match="Signature verification failed"):
            remote_connection_service.handle_connection_request(db_session, request_data)
