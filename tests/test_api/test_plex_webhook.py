"""API tests for Plex webhook + settings."""

import httpx
import pytest
import respx

from app.services.plex_service import get_or_create_plex_config


def test_get_plex_settings(authenticated_client, db_session):
    response = authenticated_client.get("/api/v1/plex/settings")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "fridgefs_mounted" in data


def test_update_plex_settings(authenticated_client, db_session):
    response = authenticated_client.put(
        "/api/v1/plex/settings",
        json={
            "enabled": True,
            "base_url": "http://plex:32400",
            "token": "secret",
            "webhook_token": "whsec",
            "path_maps": [{"plex_prefix": "/data", "fridge_prefix": "/hot"}],
            "library_maps": [{"section_id": "1", "path_id": 1, "mode": "symlink_webhook"}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["has_token"] is True
    assert data["webhook_token"] == "whsec"
    assert data["path_maps"][0]["plex_prefix"] == "/data"


def test_webhook_requires_token_when_configured(client, db_session):
    config = get_or_create_plex_config(db_session)
    config.enabled = True
    config.webhook_token = "expected"
    db_session.add(config)
    db_session.commit()

    bad = client.post("/api/v1/plex/webhook", json={"event": "media.play"})
    assert bad.status_code == 401

    ok = client.post(
        "/api/v1/plex/webhook?token=expected",
        json={"event": "media.play", "Metadata": {"ratingKey": "1"}, "Server": {}},
    )
    assert ok.status_code == 200
    assert ok.json()["handled"] is True


@respx.mock
def test_fridgefs_status(authenticated_client, db_session):
    response = authenticated_client.get("/api/v1/plex/fridgefs/status")
    assert response.status_code == 200
    body = response.json()
    assert "mounted" in body
    assert "available" in body
