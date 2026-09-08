"""Tests for P2P request-bound HMAC authentication."""

import time

from app.utils.p2p_auth import LEGACY_PSK_HEADER, sign_request, verify_request


def test_sign_and_verify_request_round_trip():
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = sign_request(psk, "GET", path)

    assert verify_request(psk, "GET", path, headers)


def test_verify_request_rejects_legacy_static_bearer():
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = {
        LEGACY_PSK_HEADER: "deadbeef",
        **sign_request(psk, "GET", path),
    }

    assert not verify_request(psk, "GET", path, headers)


def test_verify_request_rejects_stale_timestamp():
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    stale_timestamp = int(time.time()) - 3600
    headers = sign_request(psk, "GET", path, timestamp=stale_timestamp)

    assert not verify_request(psk, "GET", path, headers, max_age_seconds=300)


def test_verify_request_includes_body_hash():
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest/push"
    body = b'{"host":"127.0.0.1"}'
    headers = sign_request(psk, "POST", path, body)

    assert verify_request(psk, "POST", path, headers, body)
    assert not verify_request(psk, "POST", path, headers, b'{"host":"10.0.0.1"}')
