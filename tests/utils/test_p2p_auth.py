"""Tests for P2P request-bound HMAC authentication."""

import time

from app.utils.p2p_auth import (
    LEGACY_PSK_HEADER,
    canonical_auth_path,
    sign_request,
    verify_request,
)


def test_sign_and_verify_request_round_trip():
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = sign_request(psk, "GET", path)

    assert verify_request(psk, "GET", path, headers)


def test_sign_and_verify_encoded_path_uses_decoded_canonical_form():
    psk = "test-shared-secret"
    encoded_path = "/api/v1/p2p/files/local%3A1/content"
    decoded_path = "/api/v1/p2p/files/local:1/content"

    assert canonical_auth_path(encoded_path) == decoded_path

    headers = sign_request(psk, "GET", encoded_path)
    assert verify_request(psk, "GET", decoded_path, headers)
    assert verify_request(psk, "GET", encoded_path, headers)


def test_manifest_local_callback_headers_are_signed():
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    request_headers = {
        "X-FF-LOCAL-HOST": "10.0.0.5",
        "X-FF-LOCAL-PORT": "8000",
    }
    headers = {
        **request_headers,
        **sign_request(psk, "GET", path, request_headers=request_headers),
    }

    assert verify_request(psk, "GET", path, headers)

    tampered = dict(headers)
    tampered["X-FF-LOCAL-HOST"] = "10.0.0.99"
    assert not verify_request(psk, "GET", path, tampered)


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
