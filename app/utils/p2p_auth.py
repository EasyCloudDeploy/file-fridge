"""Request-bound HMAC authentication for P2P peer endpoints."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping
from urllib.parse import unquote

from app.config import settings

P2P_SIGNATURE_HEADER = "X-FF-PSK-SIGNATURE"
P2P_TIMESTAMP_HEADER = "X-FF-PSK-TIMESTAMP"
LEGACY_PSK_HEADER = "X-FF-PSK"
P2P_SIGNED_REQUEST_HEADERS = (
    "X-FF-LOCAL-HOST",
    "X-FF-LOCAL-PORT",
)


def canonical_auth_path(path: str) -> str:
    """Return the decoded request path used for signing and verification."""
    normalized = path if path.startswith("/") else f"/{path}"
    return unquote(normalized)


def canonical_signed_request_headers(headers: Mapping[str, str]) -> str:
    """Serialize optional P2P request headers included in the signature."""
    lines: list[str] = []
    for name in P2P_SIGNED_REQUEST_HEADERS:
        value = headers.get(name)
        if value:
            lines.append(f"{name}:{value}")
    return "\n".join(lines)


def canonical_message(
    timestamp: int,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    signed_request_headers: str = "",
) -> str:
    """Build the canonical string signed for a P2P peer request."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method.upper()}\n{canonical_auth_path(path)}\n{body_hash}"
    if signed_request_headers:
        message = f"{message}\n{signed_request_headers}"
    return message


def sign_request(
    psk: str,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    timestamp: int | None = None,
    request_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return request-bound auth headers for an outbound P2P call."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_headers = canonical_signed_request_headers(request_headers or {})
    message = canonical_message(
        ts,
        method,
        path,
        body,
        signed_request_headers=signed_headers,
    )
    signature = hmac.new(psk.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        P2P_TIMESTAMP_HEADER: str(ts),
        P2P_SIGNATURE_HEADER: signature,
    }


def verify_request(
    psk: str,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes = b"",
    *,
    max_age_seconds: int | None = None,
) -> bool:
    """Verify HMAC auth headers for an inbound P2P call."""
    if LEGACY_PSK_HEADER in headers:
        return False

    signature = headers.get(P2P_SIGNATURE_HEADER)
    timestamp_raw = headers.get(P2P_TIMESTAMP_HEADER)
    if not signature or not timestamp_raw:
        return False

    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False

    tolerance = (
        max_age_seconds if max_age_seconds is not None else settings.signature_timestamp_tolerance
    )
    if abs(time.time() - timestamp) > tolerance:
        return False

    signed_headers = canonical_signed_request_headers(headers)
    expected = hmac.new(
        psk.encode("utf-8"),
        canonical_message(
            timestamp,
            method,
            path,
            body,
            signed_request_headers=signed_headers,
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
