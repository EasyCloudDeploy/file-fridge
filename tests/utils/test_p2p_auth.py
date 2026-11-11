"""Tests for P2P request-bound HMAC authentication."""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.utils.p2p_auth import (
    LEGACY_PSK_HEADER,
    P2P_SIGNATURE_HEADER,
    P2P_TIMESTAMP_HEADER,
    canonical_auth_path,
    sign_request,
    verify_request,
)


def test_sign_and_verify_request_round_trip() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = sign_request(psk, "GET", path)

    assert verify_request(psk, "GET", path, headers)


def test_sign_and_verify_encoded_path_uses_decoded_canonical_form() -> None:
    psk = "test-shared-secret"
    encoded_path = "/api/v1/p2p/files/local%3A1/content"
    decoded_path = "/api/v1/p2p/files/local:1/content"

    assert canonical_auth_path(encoded_path) == decoded_path

    headers = sign_request(psk, "GET", encoded_path)
    assert verify_request(psk, "GET", decoded_path, headers)
    assert verify_request(psk, "GET", encoded_path, headers)


def test_manifest_local_callback_headers_are_signed() -> None:
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


def test_verify_request_rejects_legacy_static_bearer() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = {
        LEGACY_PSK_HEADER: "deadbeef",
        **sign_request(psk, "GET", path),
    }

    assert not verify_request(psk, "GET", path, headers)


def test_verify_request_rejects_stale_timestamp() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    stale_timestamp = int(time.time()) - 3600
    headers = sign_request(psk, "GET", path, timestamp=stale_timestamp)

    assert not verify_request(psk, "GET", path, headers, max_age_seconds=300)


def test_verify_request_includes_body_hash() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest/push"
    body = b'{"host":"127.0.0.1"}'
    headers = sign_request(psk, "POST", path, body)

    assert verify_request(psk, "POST", path, headers, body)
    assert not verify_request(psk, "POST", path, headers, b'{"host":"10.0.0.1"}')


def test_verify_request_rejects_missing_or_malformed_auth_headers() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    valid = sign_request(psk, "GET", path)

    assert not verify_request(psk, "GET", path, {P2P_TIMESTAMP_HEADER: valid[P2P_TIMESTAMP_HEADER]})
    assert not verify_request(psk, "GET", path, {P2P_SIGNATURE_HEADER: valid[P2P_SIGNATURE_HEADER]})
    assert not verify_request(
        psk,
        "GET",
        path,
        {
            P2P_TIMESTAMP_HEADER: "not-an-int",
            P2P_SIGNATURE_HEADER: valid[P2P_SIGNATURE_HEADER],
        },
    )


def test_verify_request_rejects_method_or_path_mismatch() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = sign_request(psk, "GET", path)

    assert not verify_request(psk, "POST", path, headers)
    assert not verify_request(psk, "GET", "/api/v1/p2p/manifest/push", headers)


def test_verify_request_rejects_non_ascii_signature() -> None:
    psk = "test-shared-secret"
    path = "/api/v1/p2p/manifest"
    headers = sign_request(psk, "GET", path)
    headers[P2P_SIGNATURE_HEADER] = "café-not-hex"

    assert not verify_request(psk, "GET", path, headers)


def test_p2p_transport_defaults_are_secure() -> None:
    defaults = Settings.model_construct(
        secret_key="unused",
        encryption_key_file="./test_encryption.key",
    )
    assert defaults.p2p_transport_scheme == "https"
    assert defaults.p2p_tls_verify is True
    assert defaults.p2p_listen_host == "127.0.0.1"


def test_p2p_transport_scheme_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "unused-secret-key")
    monkeypatch.setenv("P2P_TRANSPORT_SCHEME", "htps")
    with pytest.raises(Exception):
        Settings(_env_file=None)
