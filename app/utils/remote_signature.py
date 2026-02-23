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


def escape_component(s: str) -> str:
    """
    Escape delimiters in message components to prevent canonicalization attacks.
    Replaces '%' with '%25' and '|' with '%7C'.
    """
    if not s:
        return ""
    # Must replace % first to avoid double escaping
    return s.replace("%", "%25").replace("|", "%7C")


def build_message_to_sign(
    method: str,
    path: str,
    query_params: str,
    body: bytes,
    timestamp: str,
    fingerprint: str,
    nonce: str,
) -> bytes:
    """
    Construct a canonical message from request components for signing.
    This ensures both client and server sign the exact same payload.
    The order and format are crucial and must be identical on both ends.

    Components are escaped to prevent delimiter injection (canonicalization attacks).
    """
    body_hash = hashlib.sha256(body).hexdigest()

    # Escape all string components that might contain delimiters
    # method, timestamp, fingerprint, nonce, body_hash are typically safe characters
    # but we escape them for consistency and future-proofing.
    # path and query_params are critical to escape.
    e_method = escape_component(method.upper())
    e_path = escape_component(path)
    e_query = escape_component(query_params)
    e_timestamp = escape_component(timestamp)
    e_fingerprint = escape_component(fingerprint)
    e_nonce = escape_component(nonce)
    e_body_hash = escape_component(body_hash)

    return (
        f"{e_method}|{e_path}|{e_query}|{e_timestamp}|{e_fingerprint}|{e_nonce}|{e_body_hash}"
    ).encode()


async def get_signed_headers(db: Session, method: str, url: str, content: bytes) -> Dict[str, str]:
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

    message = build_message_to_sign(
        method, parsed_url.path, parsed_url.query.decode(), content, timestamp, fingerprint, nonce
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
    return await verify_signature_from_components(
        db, x_fingerprint, x_timestamp, x_signature, x_nonce, request, body
    )
