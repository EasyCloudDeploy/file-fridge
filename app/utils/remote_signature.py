"""Utility for signing and verifying inter-instance API requests."""

import hashlib
import logging
import time
from typing import Dict

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import RemoteConnection, TrustStatus
from app.services.identity_service import identity_service
from app.services.remote_connection_service import remote_connection_service

logger = logging.getLogger(__name__)

# The maximum age of a request timestamp in seconds
# This is now configurable via settings.signature_timestamp_tolerance

# Headers that MUST be signed if present in the request to prevent tampering
SIGNED_LOGIC_HEADERS = {
    "x-chunk-index",
    "x-relative-path",
    "x-remote-path-id",
    "x-storage-type",
    "x-job-id",
    "x-is-final",
    "x-encryption-nonce",
    "x-ephemeral-public-key",
    "x-file-size",
}


def build_message_to_sign(
    method: str,
    path: str,
    query_params: str,
    body: bytes,
    timestamp: str,
    fingerprint: str,
    nonce: str,
    headers: Dict[str, str] = None,
) -> bytes:
    """
    Construct a canonical message from request components for signing.
    This ensures both client and server sign the exact same payload.
    The order and format are crucial and must be identical on both ends.

    Args:
        headers: Optional dictionary of extra headers to include in signature.
                Keys should be lowercase.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    parts = [
        method.upper(),
        path,
        query_params,
        timestamp,
        fingerprint,
        nonce,
        body_hash,
    ]

    if headers:
        # Sort headers by key to ensure deterministic order
        for key in sorted(headers.keys()):
            parts.append(f"{key.lower()}={headers[key]}")

    return "|".join(parts).encode()


async def get_signed_headers(
    db: Session,
    method: str,
    url: str,
    content: bytes,
    extra_headers: Dict[str, str] = None,
) -> Dict[str, str]:
    """
    Generate the necessary headers for a signed inter-instance request.
    This is the client-side part of the signature process.
    """
    import secrets

    from httpx import URL

    parsed_url = URL(url)
    timestamp = str(int(time.time()))
    fingerprint = identity_service.get_instance_fingerprint(db)
    nonce = secrets.token_hex(16)  # 32-char hex string for replay protection

    # Normalize extra headers to lowercase keys for signing
    headers_to_sign = {}
    if extra_headers:
        headers_to_sign = {k.lower(): str(v) for k, v in extra_headers.items()}

    message = build_message_to_sign(
        method,
        parsed_url.path,
        parsed_url.query.decode(),
        content,
        timestamp,
        fingerprint,
        nonce,
        headers_to_sign,
    )
    signature = identity_service.sign_message(db, message)

    return {
        "X-Fingerprint": fingerprint,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature.hex(),
    }


async def verify_signature_from_components(
    db: Session,
    fingerprint: str,
    timestamp_str: str,
    signature_hex: str,
    nonce: str,
    request: Request,
    body: bytes,
    headers: Dict[str, str] = None,
) -> RemoteConnection:
    """
    Core verification logic with nonce-based replay protection.
    """
    # 1. Check timestamp to prevent replay attacks
    try:
        timestamp = int(timestamp_str)
        current_time = int(time.time())
        if abs(current_time - timestamp) > settings.signature_timestamp_tolerance:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Request timestamp is too old."
            )
    except (ValueError, TypeError) as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid timestamp format."
        ) from err

    # 2. Check nonce hasn't been used (replay protection)
    from app.models import RequestNonce
    from app.services.security_audit_service import security_audit_service

    existing_nonce = (
        db.query(RequestNonce)
        .filter(RequestNonce.nonce == nonce, RequestNonce.fingerprint == fingerprint)
        .first()
    )

    if existing_nonce:
        security_audit_service.log_replay_attack_detected(db, fingerprint, nonce)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Request nonce already used (replay attack detected)",
        )

    # 3. Look up the remote connection by its fingerprint
    conn = remote_connection_service.get_connection_by_fingerprint(db, fingerprint)
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown remote instance fingerprint."
        )
    if conn.trust_status != TrustStatus.TRUSTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Remote instance is not trusted. Current status: {conn.trust_status.value}",
        )

    # 4. Reconstruct the message and verify the signature
    message = build_message_to_sign(
        request.method,
        request.url.path,
        request.url.query,
        body,
        timestamp_str,
        fingerprint,
        nonce,
        headers,
    )
    signature_bytes = bytes.fromhex(signature_hex)

    if not identity_service.verify_signature(
        conn.remote_ed25519_public_key, signature_bytes, message
    ):
        security_audit_service.log_signature_verification_failed(
            db, fingerprint, "Invalid signature"
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature.")

    # 5. Store nonce to prevent replay
    request_nonce = RequestNonce(fingerprint=fingerprint, nonce=nonce, timestamp=timestamp)
    db.add(request_nonce)
    db.commit()

    return conn


async def verify_remote_signature(
    request: Request,
    x_fingerprint: str = Header(..., alias="X-Fingerprint"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
    x_nonce: str = Header(..., alias="X-Nonce"),
    x_signature: str = Header(..., alias="X-Signature"),
    db: Session = Depends(get_db),
) -> RemoteConnection:
    """
    A FastAPI dependency that verifies the signature of an incoming request.
    It reads the request body, so it cannot be used on endpoints that also need to read the body.
    """
    body = await request.body()

    # Extract critical logic headers that must be signed
    headers_to_sign = {}
    for key in request.headers:
        if key.lower() in SIGNED_LOGIC_HEADERS:
            headers_to_sign[key.lower()] = request.headers[key]

    return await verify_signature_from_components(
        db,
        x_fingerprint,
        x_timestamp,
        x_signature,
        x_nonce,
        request,
        body,
        headers_to_sign,
    )
