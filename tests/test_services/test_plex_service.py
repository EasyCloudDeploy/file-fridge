"""Tests for Plex PMS client helpers."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.models import PlexIntegrationConfig
from app.services.plex_service import PlexService, get_or_create_plex_config


@pytest.fixture
def plex_config(db_session):
    config = get_or_create_plex_config(db_session)
    config.enabled = True
    config.base_url = "http://plex.test:32400"
    config.set_token("test-token")
    config.set_path_maps([{"plex_prefix": "/data/media", "fridge_prefix": "/hot/media"}])
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)
    return config


@respx.mock
def test_test_connection(plex_config):
    respx.get("http://plex.test:32400/identity").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"machineIdentifier": "abc", "version": "1.40.0"}},
        )
    )
    service = PlexService(plex_config)
    result = service.test_connection()
    assert result["ok"] is True
    assert result["machine_identifier"] == "abc"


@respx.mock
def test_get_item_parts(plex_config):
    respx.get("http://plex.test:32400/library/metadata/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "ratingKey": "42",
                            "title": "Movie",
                            "lastViewedAt": 1_700_000_000,
                            "viewCount": 3,
                            "Media": [
                                {
                                    "Part": [
                                        {
                                            "id": 1,
                                            "file": "/data/media/movie.mkv",
                                            "size": 100,
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                }
            },
        )
    )
    service = PlexService(plex_config)
    parts = service.get_item_parts(42)
    assert len(parts) == 1
    assert parts[0]["file"] == "/data/media/movie.mkv"
    assert service.map_plex_path_to_fridge(parts[0]["file"]) == "/hot/media/movie.mkv"


def test_plex_epoch_to_datetime():
    dt = PlexService.plex_epoch_to_datetime(1_700_000_000)
    assert isinstance(dt, datetime)
    assert dt.tzinfo == UTC
    assert PlexService.plex_epoch_to_datetime(None) is None


def test_get_or_create_singleton(db_session):
    a = get_or_create_plex_config(db_session)
    b = get_or_create_plex_config(db_session)
    assert a.id == b.id
    assert isinstance(a, PlexIntegrationConfig)
