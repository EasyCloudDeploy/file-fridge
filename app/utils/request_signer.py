"""Request signing utilities for replay protection."""

import hashlib
import hmac
import time
from typing import Tuple


def sign_request(shared_secret: str, data: str) -> Tuple[str, int]:
    """
    Sign a request with timestamp for replay protection.

    Args:
        shared_secret: The shared secret for authentication
        data: The request data to sign

    Returns:
        Tuple of (signature_hex, timestamp)
    """
    timestamp = int(time.time())
    message = f"{data}{timestamp}"

    signature = hmac.new(bytes.fromhex(shared_secret), message.encode(), hashlib.sha256).hexdigest()

    return signature, timestamp


def verify_signed_request(
    shared_secret: str,
    data: str,
    signature: str,
    timestamp: int,
    max_age_seconds: int = 300,
) -> bool:
    """
    Verify a signed request and check timestamp freshness.

    Args:
        shared_secret: The shared secret
        data: The original request data
        signature: The HMAC signature
        timestamp: Request timestamp
        max_age_seconds: Maximum age for requests (default 5 minutes)

    Returns:
        bool: True if signature is valid and timestamp is recent
    """
    current_time = time.time()

    # Check timestamp freshness
    if abs(current_time - timestamp) > max_age_seconds:
        return False

    # Verify signature
    expected_signature = hmac.new(
        bytes.fromhex(shared_secret), f"{data}{timestamp}".encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature)
