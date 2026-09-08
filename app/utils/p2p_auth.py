"""Request-bound HMAC authentication for P2P peer endpoints."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

from app.config import settings

P2P_SIGNATURE_HEADER = "X-FF-PSK-SIGNATURE"
P2P_TIMESTAMP_HEADER = "X-FF-PSK-TIMESTAMP"
LEGACY_PSK_HEADER = "X-FF-PSK"


def canonical_message(timestamp: int, method: str, path: str, body: bytes = b"") -> str:
    """Build the canonical string signed for a P2P peer request."""
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}"


def sign_request(
    psk: str,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Return request-bound auth headers for an outbound P2P call."""
    ts = timestamp if timestamp is not None else int(time.time())
    message = canonical_message(ts, method, path, body)
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

    expected = hmac.new(
        psk.encode("utf-8"),
        canonical_message(timestamp, method, path, body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
