"""Plex integration API: settings, webhooks, FridgeFS control."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PlexLibraryMap, PlexPathMap, PlexSettingsResponse, PlexSettingsUpdate
from app.services.fridgefs.manager import fridgefs_manager
from app.services.plex_policy import PlexPolicy
from app.services.plex_service import PlexService, PlexServiceError, get_or_create_plex_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/plex", tags=["plex"])
# Webhook is public (Plex/Tautulli cannot send File Fridge session cookies)
webhook_router = APIRouter(prefix="/api/v1/plex", tags=["plex-webhook"])


def _settings_response(config, mounted: bool = False) -> PlexSettingsResponse:
    return PlexSettingsResponse(
        enabled=bool(config.enabled),
        base_url=config.base_url,
        has_token=bool(config.token_encrypted),
        webhook_token=config.webhook_token,
        server_uuid=config.server_uuid,
        tautulli_enabled=bool(config.tautulli_enabled),
        library_maps=[PlexLibraryMap(**m) for m in config.get_library_maps()],
        path_maps=[PlexPathMap(**m) for m in config.get_path_maps()],
        thaw_on_play=bool(config.thaw_on_play),
        refresh_on_freeze=bool(config.refresh_on_freeze),
        refresh_on_thaw=bool(config.refresh_on_thaw),
        apply_cold_label=bool(config.apply_cold_label),
        cold_label=config.cold_label,
        prethaw_on_deck=bool(config.prethaw_on_deck),
        poll_watch_state=bool(config.poll_watch_state),
        fridgefs_enabled=bool(config.fridgefs_enabled),
        fridgefs_mount_path=config.fridgefs_mount_path,
        fridgefs_cache_dir=config.fridgefs_cache_dir,
        fridgefs_cache_max_gb=int(config.fridgefs_cache_max_gb or 20),
        fridgefs_allow_other=bool(config.fridgefs_allow_other),
        fridgefs_mounted=mounted,
    )


@router.get("/settings", response_model=PlexSettingsResponse)
def get_plex_settings(db: Annotated[Session, Depends(get_db)]):
    config = get_or_create_plex_config(db)
    return _settings_response(config, mounted=fridgefs_manager.is_mounted())


@router.put("/settings", response_model=PlexSettingsResponse)
def update_plex_settings(payload: PlexSettingsUpdate, db: Annotated[Session, Depends(get_db)]):
    config = get_or_create_plex_config(db)
    data = payload.model_dump(exclude_unset=True)
    if "token" in data:
        token = data.pop("token")
        if token is not None:
            config.set_token(token)
    if "library_maps" in data:
        maps = data.pop("library_maps") or []
        config.set_library_maps([m if isinstance(m, dict) else m.model_dump() for m in maps])
    if "path_maps" in data:
        maps = data.pop("path_maps") or []
        config.set_path_maps([m if isinstance(m, dict) else m.model_dump() for m in maps])
    for key, value in data.items():
        if hasattr(config, key):
            setattr(config, key, value)
    db.add(config)
    db.commit()
    db.refresh(config)
    return _settings_response(config, mounted=fridgefs_manager.is_mounted())


@router.post("/test")
def test_plex_connection(db: Annotated[Session, Depends(get_db)]):
    plex = PlexService.from_db(db)
    if not plex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plex is not configured (enable + base_url + token required)",
        )
    try:
        identity = plex.test_connection()
        sections = plex.list_sections()
        return {
            "ok": True,
            "identity": identity,
            "sections": [
                {
                    "key": s.get("key"),
                    "title": s.get("title"),
                    "type": s.get("type"),
                    "locations": [loc.get("path") for loc in (s.get("Location") or [])],
                }
                for s in sections
            ],
        }
    except PlexServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/prethaw-on-deck")
def prethaw_on_deck(db: Annotated[Session, Depends(get_db)]):
    return PlexPolicy.prethaw_on_deck(db)


@router.post("/sync-watch-state")
def sync_watch_state(db: Annotated[Session, Depends(get_db)]):
    return PlexPolicy.sync_watch_state(db)


@router.get("/fridgefs/status")
def fridgefs_status(db: Annotated[Session, Depends(get_db)]):
    config = get_or_create_plex_config(db)
    return {
        "enabled": bool(config.fridgefs_enabled),
        "mount_path": config.fridgefs_mount_path,
        "mounted": fridgefs_manager.is_mounted(),
        "cache": fridgefs_manager.cache_stats(),
        "available": fridgefs_manager.fuse_available(),
    }


@router.post("/fridgefs/mount")
def fridgefs_mount(db: Annotated[Session, Depends(get_db)]):
    config = get_or_create_plex_config(db)
    if not config.fridgefs_enabled:
        raise HTTPException(status_code=400, detail="FridgeFS is disabled in settings")
    try:
        fridgefs_manager.mount(db, config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"mounted": fridgefs_manager.is_mounted(), "mount_path": config.fridgefs_mount_path}


@router.post("/fridgefs/unmount")
def fridgefs_unmount():
    fridgefs_manager.unmount()
    return {"mounted": fridgefs_manager.is_mounted()}


def _extract_webhook_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize Plex native and Tautulli webhook payloads."""
    # Native Plex: {"event": "media.play", "Metadata": {...}, "Server": {...}}
    if "event" in raw or "Metadata" in raw:
        metadata = raw.get("Metadata") or {}
        server = raw.get("Server") or {}
        return {
            "event": raw.get("event") or "media.play",
            "rating_key": metadata.get("ratingKey"),
            "server_uuid": server.get("uuid"),
            "file": None,
        }
    # Tautulli often nests under "data" or uses flat keys
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    event = data.get("action") or data.get("event") or raw.get("action") or "media.play"
    # Normalize tautulli actions: play/resume/stop/watched
    mapping = {
        "play": "media.play",
        "resume": "media.resume",
        "stop": "media.stop",
        "watched": "media.scrobble",
        "scrobble": "media.scrobble",
    }
    event = mapping.get(str(event).lower(), event)
    return {
        "event": event,
        "rating_key": data.get("rating_key") or data.get("ratingKey"),
        "server_uuid": data.get("server_uuid") or data.get("machine_id"),
        "file": data.get("file") or data.get("file_path") or data.get("media_file"),
    }


@webhook_router.post("/webhook")
async def plex_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Query()] = None,
    x_file_fridge_plex_token: Annotated[str | None, Header()] = None,
):
    config = get_or_create_plex_config(db)
    if not config.enabled:
        raise HTTPException(status_code=503, detail="Plex integration disabled")

    provided = token or x_file_fridge_plex_token
    if config.webhook_token and provided != config.webhook_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    content_type = request.headers.get("content-type", "")
    raw: dict[str, Any] = {}
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            payload = form.get("payload")
            if payload:
                raw = json.loads(str(payload))
        else:
            body = await request.body()
            if body:
                raw = json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid webhook body: {exc}") from exc

    normalized = _extract_webhook_payload(raw)
    return PlexPolicy.handle_play_event(
        db,
        rating_key=normalized.get("rating_key"),
        plex_file_path=normalized.get("file"),
        event=normalized.get("event") or "media.play",
        server_uuid=normalized.get("server_uuid"),
    )


@webhook_router.post("/tautulli")
async def tautulli_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Query()] = None,
):
    """Alias endpoint for Tautulli notification agents."""
    return await plex_webhook(request, db, token=token, x_file_fridge_plex_token=None)
