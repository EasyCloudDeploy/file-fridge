"""Legacy remote API compatibility tests."""

from fastapi.testclient import TestClient


def test_legacy_remote_protocol_is_rejected(client: TestClient):
    response = client.get('/api/v1/remote/identity')
    assert response.status_code == 426
    assert 'P2P v2' in response.json()['detail']


def test_legacy_remote_root_is_rejected(client: TestClient):
    response = client.get('/api/v1/remote')
    assert response.status_code == 426
