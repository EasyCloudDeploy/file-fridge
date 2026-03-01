import hashlib
import time
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from fastapi import HTTPException
from app.utils.remote_signature import build_message_to_sign, verify_signature_from_components
from app.models import RemoteConnection, TrustStatus

def test_canonicalization_collision_fixed():
    """
    Test that the canonicalization vulnerability is fixed.
    Inputs that previously collided should now produce different messages.
    """
    method = "GET"
    body = b""
    timestamp = "1234567890"
    fingerprint = "test-fingerprint"
    nonce = "test-nonce"

    # Case 1: path has pipe, empty query
    # Escaped: /foo%7Cbar
    path1 = "/foo|bar"
    query1 = ""
    msg1 = build_message_to_sign(method, path1, query1, body, timestamp, fingerprint, nonce)

    # Case 2: path has no pipe, query has pipe
    # Escaped: /foo, bar%7C
    path2 = "/foo"
    query2 = "bar|"
    msg2 = build_message_to_sign(method, path2, query2, body, timestamp, fingerprint, nonce)

    print(f"Msg1: {msg1}")
    print(f"Msg2: {msg2}")

    assert msg1 != msg2, "Canonicalization collision detected!"

    # Verify escaping works as expected
    # Message format: METHOD|PATH|QUERY|TIMESTAMP|FINGERPRINT|NONCE|HASH
    parts1 = msg1.split(b"|")
    # parts1[0] = METHOD
    # parts1[1] = PATH
    # parts1[2] = QUERY
    assert parts1[1] == b"/foo%7Cbar"
    assert parts1[2] == b""

    parts2 = msg2.split(b"|")
    assert parts2[1] == b"/foo"
    assert parts2[2] == b"bar%7C"

@pytest.mark.asyncio
async def test_verify_signature_escaped():
    """
    Test that verify_signature_from_components correctly verifies a request signed with the escaped format.
    """
    db = MagicMock()
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/foo|bar"
    request.url.query = ""
    body = b"test-body"
    timestamp = str(int(time.time()))
    fingerprint = "test-fingerprint"
    nonce = "test-nonce"
    signature_hex = "deadbeef" # Mock signature

    # Mock services
    with patch("app.utils.remote_signature.identity_service") as mock_identity_service, \
         patch("app.utils.remote_signature.remote_connection_service") as mock_conn_service:

        # Mock RequestNonce query (no replay)
        db.query.return_value.filter.return_value.first.return_value = None

        # Mock Connection lookup
        mock_conn = MagicMock(spec=RemoteConnection)
        mock_conn.trust_status = TrustStatus.TRUSTED
        mock_conn.remote_ed25519_public_key = "pubkey"
        mock_conn_service.get_connection_by_fingerprint.return_value = mock_conn

        # Mock signature verification
        # It should verify TRUE if the message matches what verify_signature_from_components constructs
        mock_identity_service.verify_signature.return_value = True

        # Call the function
        result = await verify_signature_from_components(
            db, fingerprint, timestamp, signature_hex, nonce, request, body
        )

        assert result == mock_conn

        # Verify the message that was constructed and verified
        # verify_signature calls identity_service.verify_signature(pubkey, sig_bytes, message)
        args, _ = mock_identity_service.verify_signature.call_args
        verified_message = args[2]

        expected_message = build_message_to_sign(
            request.method, request.url.path, request.url.query, body, timestamp, fingerprint, nonce
        )

        assert verified_message == expected_message
        assert b"%7C" in verified_message
