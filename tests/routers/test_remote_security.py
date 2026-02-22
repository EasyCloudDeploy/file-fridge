
import time
import logging
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import pytest
from fastapi import Request, HTTPException

from app.models import RemoteConnection, TrustStatus
from app.utils.remote_signature import verify_remote_signature

logger = logging.getLogger(__name__)


# Mock the database dependency
@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.method = "POST"
    request.url.path = "/api/v1/remote/receive"
    request.url.query = ""
    request.body = AsyncMock(return_value=b"test-content")
    return request


@pytest.fixture
def valid_connection():
    conn = MagicMock(spec=RemoteConnection)
    conn.id = 1
    conn.name = "Test Remote"
    conn.remote_fingerprint = "fingerprint123"
    conn.trust_status = TrustStatus.TRUSTED
    conn.remote_ed25519_public_key = "remote_pubkey"
    return conn


@patch("app.utils.remote_signature.identity_service")
@patch("app.utils.remote_signature.remote_connection_service")
@patch("app.services.security_audit_service.security_audit_service")
@patch("app.models.RequestNonce")  # Mock the model class
@pytest.mark.asyncio
async def test_header_integrity_vulnerability(
    mock_request_nonce_model,
    mock_audit,
    mock_rc_service,
    mock_identity,
    mock_db,
    mock_request,
    valid_connection,
):
    """
    Demonstrate that current signature verification DOES cover X- headers.
    We verify this by ensuring the signed message changes when a header changes.
    """
    # Setup mocks
    mock_rc_service.get_connection_by_fingerprint.return_value = valid_connection
    # We want verify_signature to succeed so we can inspect the call arguments
    mock_identity.verify_signature.return_value = True

    # Mock database query for nonce check (return None = no replay)
    mock_db.query.return_value.filter.return_value.first.return_value = None

    # Construct the headers
    timestamp = str(int(time.time()))
    nonce = "nonce123"
    fingerprint = "fingerprint123"
    signature = "deadbeef" * 8

    original_headers = {
        "X-Fingerprint": fingerprint,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "X-Relative-Path": "safe_file.txt",
        "X-Chunk-Index": "0",
    }

    mock_request.headers = original_headers.copy()

    # 1. Verify with original headers
    await verify_remote_signature(
        mock_request,
        x_fingerprint=fingerprint,
        x_timestamp=timestamp,
        x_nonce=nonce,
        x_signature=signature,
        db=mock_db,
    )

    # Capture the message signed in the first call
    # call_args[0] are positional args: (public_key, signature, message)
    args1 = mock_identity.verify_signature.call_args[0]
    message1 = args1[2]
    print(f"Message 1: {message1}")

    # 2. Tamper with X-Relative-Path
    tampered_headers = original_headers.copy()
    tampered_headers["X-Relative-Path"] = "CRITICAL_SYSTEM_FILE.txt"
    mock_request.headers = tampered_headers

    # 3. Verify again
    await verify_remote_signature(
        mock_request,
        x_fingerprint=fingerprint,
        x_timestamp=timestamp,
        x_nonce=nonce,
        x_signature=signature,
        db=mock_db,
    )

    # Capture the message signed in the second call
    args2 = mock_identity.verify_signature.call_args[0]
    message2 = args2[2]
    print(f"Message 2: {message2}")

    # 4. Assert that the messages are DIFFERENT
    # If the vulnerability exists (headers ignored), message1 would equal message2
    assert message1 != message2, "Vulnerability confirmed: Tampered header did not change signed message!"

    # 5. Verify that the tampered header value is actually present in message2
    assert b"CRITICAL_SYSTEM_FILE.txt" in message2

    # 6. Verify that the original header value is present in message1
    assert b"safe_file.txt" in message1

    # In a real scenario, since the signature (X-Signature) didn't change but the message did,
    # the crypto verification would fail. Since we mocked it to True, it passed,
    # but we proved that the input to the crypto function is correct.
