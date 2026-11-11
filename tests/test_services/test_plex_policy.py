"""Tests for Plex playback policy and plex_last_played criterion."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from app.models import (
    Criteria,
    CriterionType,
    FileInventory,
    FileStatus,
    MonitoredPath,
    Operator,
    StorageType,
)
from app.services.criteria_matcher import CriteriaMatcher
from app.services.plex_policy import PlexPolicy
from app.services.plex_service import get_or_create_plex_config


@pytest.fixture
def monitored_path(db_session, tmp_path):
    hot = tmp_path / "hot"
    hot.mkdir()
    path = MonitoredPath(name="media", source_path=str(hot), enabled=True)
    db_session.add(path)
    db_session.commit()
    db_session.refresh(path)
    return path


@pytest.fixture
def inventory_entry(db_session, monitored_path, tmp_path):
    file_path = Path(monitored_path.source_path) / "movie.mkv"
    file_path.write_bytes(b"data")
    entry = FileInventory(
        path_id=monitored_path.id,
        file_path=str(file_path),
        file_size=4,
        file_mtime=datetime.now(UTC),
        storage_type=StorageType.HOT,
        status=FileStatus.ACTIVE,
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return entry


def test_playback_lock_roundtrip():
    PlexPolicy.release_playback_lock("path:/tmp/x")
    assert PlexPolicy.is_path_playback_locked("/tmp/x") is False
    PlexPolicy.acquire_playback_lock("path:/tmp/x")
    assert PlexPolicy.is_path_playback_locked("/tmp/x") is True
    PlexPolicy.release_playback_lock("path:/tmp/x")
    assert PlexPolicy.is_path_playback_locked("/tmp/x") is False


def test_update_play_stats(db_session, inventory_entry):
    PlexPolicy.update_play_stats(
        db_session, inventory_entry, rating_key="99", played_at=datetime.now(UTC)
    )
    db_session.commit()
    db_session.refresh(inventory_entry)
    assert inventory_entry.plex_rating_key == "99"
    assert inventory_entry.last_plex_played_at is not None
    assert inventory_entry.plex_play_count == 1


@respx.mock
def test_handle_play_event_updates_inventory(db_session, inventory_entry, monitored_path):
    config = get_or_create_plex_config(db_session)
    config.enabled = True
    config.base_url = "http://plex.test:32400"
    config.set_token("tok")
    config.thaw_on_play = False
    db_session.add(config)
    db_session.commit()

    respx.get("http://plex.test:32400/library/metadata/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "ratingKey": "7",
                            "title": "Movie",
                            "Media": [{"Part": [{"id": 1, "file": inventory_entry.file_path}]}],
                        }
                    ]
                }
            },
        )
    )

    result = PlexPolicy.handle_play_event(db_session, rating_key="7", event="media.play")
    assert result["handled"] is True
    db_session.refresh(inventory_entry)
    assert inventory_entry.plex_rating_key == "7"
    assert inventory_entry.last_plex_played_at is not None


def test_plex_last_played_criterion_keep_hot(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")
    recent = datetime.now(UTC) - timedelta(minutes=10)
    criterion = Criteria(
        path_id=1,
        criterion_type=CriterionType.PLEX_LAST_PLAYED,
        operator=Operator.LT,
        value="60",
        enabled=True,
    )
    # Criteria ORM object without DB session — matcher only reads fields
    ok, ids = CriteriaMatcher.match_file(
        path, [criterion], inventory_meta={"last_plex_played_at": recent}
    )
    assert ok is True
    assert criterion.id in ids or ids == [None]

    old = datetime.now(UTC) - timedelta(days=40)
    ok, _ = CriteriaMatcher.match_file(
        path, [criterion], inventory_meta={"last_plex_played_at": old}
    )
    assert ok is False


def test_find_inventory_for_plex_path(db_session, inventory_entry):
    found = PlexPolicy.find_inventory_for_plex_path(db_session, inventory_entry.file_path)
    assert found is not None
    assert found.id == inventory_entry.id
