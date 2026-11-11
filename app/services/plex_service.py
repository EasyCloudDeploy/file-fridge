"""Bidirectional Plex Media Server client."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import PlexIntegrationConfig

logger = logging.getLogger(__name__)


class PlexServiceError(Exception):
    """Raised when a Plex API call fails."""


class PlexService:
    """HTTP client for inbound path resolution and outbound PMS mutations."""

    def __init__(self, config: PlexIntegrationConfig):
        self.config = config
        self.base_url = (config.base_url or "").rstrip("/")
        self.token = config.get_token()

    @classmethod
    def from_db(cls, db: Session) -> PlexService | None:
        config = db.query(PlexIntegrationConfig).order_by(PlexIntegrationConfig.id.asc()).first()
        if not config or not config.enabled or not config.base_url or not config.get_token():
            return None
        return cls(config)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Plex-Token": self.token or "",
            "X-Plex-Product": "FileFridge",
            "X-Plex-Client-Identifier": "file-fridge-plex",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=self._headers(), params=params or {})
                response.raise_for_status()
                if "application/json" in response.headers.get("content-type", ""):
                    return response.json()
                return response.text
        except httpx.HTTPError as exc:
            msg = f"GET {path} failed: {exc}"
            raise PlexServiceError(msg) from exc

    def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=self._headers(), params=params or {})
                response.raise_for_status()
                if response.content and "application/json" in response.headers.get(
                    "content-type", ""
                ):
                    return response.json()
                return None
        except httpx.HTTPError as exc:
            msg = f"POST {path} failed: {exc}"
            raise PlexServiceError(msg) from exc

    def _put(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.put(url, headers=self._headers(), params=params or {})
                response.raise_for_status()
                if response.content and "application/json" in response.headers.get(
                    "content-type", ""
                ):
                    return response.json()
                return None
        except httpx.HTTPError as exc:
            msg = f"PUT {path} failed: {exc}"
            raise PlexServiceError(msg) from exc

    def test_connection(self) -> dict[str, Any]:
        data = self._get("/identity")
        media = data.get("MediaContainer", data) if isinstance(data, dict) else {}
        return {
            "ok": True,
            "machine_identifier": media.get("machineIdentifier"),
            "version": media.get("version"),
        }

    def list_sections(self) -> list[dict[str, Any]]:
        data = self._get("/library/sections")
        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        return list(container.get("Directory", []) or [])

    def get_item(self, rating_key: str | int) -> dict[str, Any]:
        data = self._get(f"/library/metadata/{rating_key}")
        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        metadata = container.get("Metadata") or []
        if not metadata:
            msg = f"No metadata for ratingKey={rating_key}"
            raise PlexServiceError(msg)
        return metadata[0]

    def get_item_parts(self, rating_key: str | int) -> list[dict[str, Any]]:
        item = self.get_item(rating_key)
        parts: list[dict[str, Any]] = []
        for media in item.get("Media") or []:
            for part in media.get("Part") or []:
                parts.append(
                    {
                        "id": part.get("id"),
                        "file": part.get("file"),
                        "size": part.get("size"),
                        "duration": part.get("duration"),
                        "rating_key": str(item.get("ratingKey")),
                        "title": item.get("title"),
                        "last_viewed_at": item.get("lastViewedAt"),
                        "view_count": item.get("viewCount"),
                    }
                )
        return parts

    def refresh_section(self, section_id: str | int) -> None:
        self._post(f"/library/sections/{section_id}/refresh")

    def refresh_item(self, rating_key: str | int) -> None:
        self._put(f"/library/metadata/{rating_key}/refresh")

    def set_label(self, rating_key: str | int, label: str) -> None:
        # Plex metadata edit: label[].tag.tag=Name
        params = {
            "label.locked": 1,
            "label[][tag].tag": label,
            "label[].tag.tag": label,
        }
        self._put(f"/library/metadata/{rating_key}", params=params)

    def ensure_collection(self, section_id: str | int, name: str) -> str | None:
        """Return existing collection ratingKey by name, or None if create is unsupported."""
        data = self._get(f"/library/sections/{section_id}/collections")
        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        for entry in container.get("Metadata") or []:
            if entry.get("title") == name:
                return str(entry.get("ratingKey"))
        return None

    def get_on_deck(self) -> list[dict[str, Any]]:
        data = self._get("/library/onDeck")
        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        return list(container.get("Metadata") or [])

    def get_continue_watching(self) -> list[dict[str, Any]]:
        # Hub-based continue watching; fall back to onDeck
        try:
            data = self._get("/hubs/continueWatching")
            container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
            hubs = container.get("Hub") or []
            items: list[dict[str, Any]] = []
            for hub in hubs:
                items.extend(hub.get("Metadata") or [])
            if items:
                return items
        except PlexServiceError:
            logger.debug("continueWatching hub unavailable; falling back to onDeck")
        return self.get_on_deck()

    def list_sessions(self) -> list[dict[str, Any]]:
        data = self._get("/status/sessions")
        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        return list(container.get("Metadata") or [])

    def map_plex_path_to_fridge(self, plex_path: str) -> str:
        """Translate a Plex-reported file path using configured prefix maps."""
        for mapping in self.config.get_path_maps():
            plex_prefix = mapping.get("plex_prefix") or ""
            fridge_prefix = mapping.get("fridge_prefix") or ""
            if plex_prefix and plex_path.startswith(plex_prefix):
                return fridge_prefix + plex_path[len(plex_prefix) :]
        return plex_path

    def poll_section_watch_state(self, section_id: str | int) -> list[dict[str, Any]]:
        """Pull lastViewedAt / viewCount for items in a library section."""
        results: list[dict[str, Any]] = []
        # Use allLeaves for TV; for movies Metadata works
        for leaf_path in (
            f"/library/sections/{section_id}/allLeaves",
            f"/library/sections/{section_id}/all",
        ):
            try:
                data = self._get(leaf_path)
            except PlexServiceError:
                continue
            container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
            for item in container.get("Metadata") or []:
                rating_key = item.get("ratingKey")
                if not rating_key:
                    continue
                try:
                    parts = self.get_item_parts(rating_key)
                except PlexServiceError:
                    continue
                for part in parts:
                    results.append(part)
            if results:
                break
        return results

    @staticmethod
    def plex_epoch_to_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (TypeError, ValueError, OSError):
            return None


def get_or_create_plex_config(db: Session) -> PlexIntegrationConfig:
    config = db.query(PlexIntegrationConfig).order_by(PlexIntegrationConfig.id.asc()).first()
    if config:
        return config
    config = PlexIntegrationConfig()
    db.add(config)
    db.commit()
    db.refresh(config)
    return config
