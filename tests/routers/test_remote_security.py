import time
import logging
from unittest.mock import AsyncMock, MagicMock, patch

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
@patch("app.utils.remote_signature.security_audit_service")
@patch("app.utils.remote_signature.RequestNonce")  # Mock the model class
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
    Demonstrate that current signature verification does NOT cover X- headers.
    This test passes if the vulnerability exists (signature verifies despite header tampering).
    """
    # Setup mocks
    mock_rc_service.get_connection_by_fingerprint.return_value = valid_connection
    mock_identity.verify_signature.return_value = (
        True  # Assume valid signature for the base message
    )

    # Mock database query for nonce check (return None = no replay)
    mock_db.query.return_value.filter.return_value.first.return_value = None

    # Construct the headers
    timestamp = str(int(time.time()))
    nonce = "nonce123"
    fingerprint = "fingerprint123"
    signature = "valid_signature_hex"

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
    try:
        result = await verify_remote_signature(
            mock_request,
            x_fingerprint=fingerprint,
            x_timestamp=timestamp,
            x_nonce=nonce,
            x_signature=signature,
            db=mock_db,
        )
        assert result == valid_connection
    except Exception as e:
        pytest.fail(f"Original headers verification FAILED: {e}")

    # 2. Tamper with X-Relative-Path
    tampered_headers = original_headers.copy()
    tampered_headers["X-Relative-Path"] = "CRITICAL_SYSTEM_FILE.txt"
    mock_request.headers = tampered_headers

    # 3. Verify again - Should fail with 401 because signature does not cover modified header
    try:
        result = await verify_remote_signature(
            mock_request,
            x_fingerprint=fingerprint,
            x_timestamp=timestamp,
            x_nonce=nonce,
            x_signature=signature,
            db=mock_db,
        )
        pytest.fail("Vulnerability still exists: Signature verified despite header tampering!")
    except HTTPException as e:
        if e.status_code == 401:
            print(f"\nSUCCESS: Tampering detected with 401: {e.detail}")
            # Test passes!
        else:
            pytest.fail(f"Verification failed with unexpected status {e.status_code}: {e.detail}")
    except Exception as e:
        pytest.fail(f"Verification failed with unexpected error: {e}")
