"""Plex playback policy: thaw-on-play, freeze guards, outbound hooks."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    FileInventory,
    FileRecord,
    FileStatus,
    PlexIntegrationConfig,
    StorageType,
)
from app.services.file_thawer import FileThawer
from app.services.plex_service import PlexService, PlexServiceError, get_or_create_plex_config

logger = logging.getLogger(__name__)

# ratingKey / inventory path locks so scans do not freeze active titles
_playback_locks: dict[str, datetime] = {}
_locks_guard = threading.Lock()
_PLAYBACK_LOCK_TTL_SECONDS = 6 * 60 * 60


class PlexPolicy:
    """Coordinates inbound play events and outbound Plex updates."""

    @staticmethod
    def acquire_playback_lock(key: str) -> None:
        with _locks_guard:
            _playback_locks[key] = datetime.now(UTC)

    @staticmethod
    def release_playback_lock(key: str) -> None:
        with _locks_guard:
            _playback_locks.pop(key, None)

    @staticmethod
    def is_playback_locked(key: str) -> bool:
        now = datetime.now(UTC)
        with _locks_guard:
            ts = _playback_locks.get(key)
            if not ts:
                return False
            age = (now - ts).total_seconds()
            if age > _PLAYBACK_LOCK_TTL_SECONDS:
                _playback_locks.pop(key, None)
                return False
            return True

    @staticmethod
    def is_path_playback_locked(file_path: str) -> bool:
        return PlexPolicy.is_playback_locked(f"path:{file_path}")

    @staticmethod
    def find_inventory_for_plex_path(
        db: Session, plex_path: str, plex: PlexService | None = None
    ) -> FileInventory | None:
        candidates = [plex_path]
        if plex:
            mapped = plex.map_plex_path_to_fridge(plex_path)
            if mapped not in candidates:
                candidates.append(mapped)
        for candidate in candidates:
            entry = (
                db.query(FileInventory)
                .filter(FileInventory.file_path == candidate)
                .order_by(FileInventory.id.desc())
                .first()
            )
            if entry:
                return entry
        # Fallback: unique basename match under known prefixes
        basename = Path(plex_path).name
        if basename:
            matches = (
                db.query(FileInventory)
                .filter(FileInventory.file_path.endswith("/" + basename))
                .limit(5)
                .all()
            )
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def update_play_stats(
        db: Session,
        inventory: FileInventory,
        *,
        rating_key: str | None = None,
        played_at: datetime | None = None,
        increment: bool = True,
    ) -> None:
        inventory.last_plex_played_at = played_at or datetime.now(UTC)
        if rating_key:
            inventory.plex_rating_key = str(rating_key)
        if increment:
            inventory.plex_play_count = int(inventory.plex_play_count or 0) + 1
        db.add(inventory)

    @staticmethod
    def thaw_inventory(
        db: Session,
        inventory: FileInventory,
        *,
        pin: bool = False,
        initiated_by: str = "plex",
    ) -> tuple[bool, str | None]:
        if inventory.storage_type != StorageType.COLD:
            return True, None
        if inventory.status == FileStatus.MIGRATING:
            return False, "thaw already in progress"

        file_record = (
            db.query(FileRecord)
            .filter(
                (FileRecord.cold_storage_path == inventory.file_path)
                | (
                    (FileRecord.path_id == inventory.path_id)
                    & (FileRecord.original_path == inventory.file_path)
                )
            )
            .first()
        )
        if not file_record:
            return False, f"no FileRecord for inventory {inventory.id}"

        inventory.status = FileStatus.MIGRATING
        db.commit()
        success, error = FileThawer.thaw_file(
            file_record, pin=pin, db=db, initiated_by=initiated_by
        )
        if not success:
            refreshed = db.query(FileInventory).filter(FileInventory.id == inventory.id).first()
            if refreshed and refreshed.storage_type == StorageType.COLD:
                refreshed.status = FileStatus.ACTIVE
                db.commit()
            return False, error
        inventory.status = FileStatus.ACTIVE
        db.commit()
        return True, None

    @staticmethod
    def handle_play_event(
        db: Session,
        *,
        rating_key: str | None,
        plex_file_path: str | None = None,
        event: str = "media.play",
        server_uuid: str | None = None,
    ) -> dict[str, Any]:
        config = get_or_create_plex_config(db)
        if not config.enabled:
            return {"handled": False, "reason": "plex integration disabled"}

        if config.server_uuid and server_uuid and server_uuid != config.server_uuid:
            return {"handled": False, "reason": "server uuid mismatch"}

        plex = PlexService.from_db(db)
        parts: list[dict[str, Any]] = []
        if rating_key and plex:
            try:
                parts = plex.get_item_parts(rating_key)
            except PlexServiceError as exc:
                logger.warning("Failed to resolve ratingKey %s: %s", rating_key, exc)

        if plex_file_path and not parts:
            parts = [{"file": plex_file_path, "rating_key": rating_key}]

        if not parts and rating_key:
            # Still lock by rating key even without path
            PlexPolicy.acquire_playback_lock(f"rk:{rating_key}")
            return {"handled": True, "thawed": [], "locked": [rating_key], "reason": "no parts"}

        thawed: list[int] = []
        locked: list[str] = []
        errors: list[str] = []

        for part in parts:
            file_path = part.get("file")
            rk = str(part.get("rating_key") or rating_key or "")
            if rk:
                PlexPolicy.acquire_playback_lock(f"rk:{rk}")
                locked.append(rk)
            if not file_path:
                continue
            PlexPolicy.acquire_playback_lock(f"path:{file_path}")
            if plex:
                mapped = plex.map_plex_path_to_fridge(file_path)
                PlexPolicy.acquire_playback_lock(f"path:{mapped}")

            inventory = PlexPolicy.find_inventory_for_plex_path(db, file_path, plex)
            if not inventory:
                errors.append(f"inventory miss for {file_path}")
                continue

            PlexPolicy.update_play_stats(
                db,
                inventory,
                rating_key=rk or None,
                played_at=datetime.now(UTC),
                increment=event in {"media.play", "media.resume", "media.scrobble"},
            )
            db.commit()

            if config.thaw_on_play and inventory.storage_type == StorageType.COLD:
                ok, err = PlexPolicy.thaw_inventory(db, inventory, initiated_by="plex-play")
                if ok:
                    thawed.append(inventory.id)
                    PlexPolicy.after_thaw(db, inventory, config=config, plex=plex)
                elif err:
                    errors.append(err)

        if event in {"media.stop"}:
            # Keep lock briefly after stop so scan lag does not immediately freeze
            pass

        return {
            "handled": True,
            "thawed": thawed,
            "locked": locked,
            "errors": errors,
            "event": event,
        }

    @staticmethod
    def after_freeze(
        db: Session,
        inventory: FileInventory,
        *,
        config: PlexIntegrationConfig | None = None,
        plex: PlexService | None = None,
    ) -> None:
        config = config or get_or_create_plex_config(db)
        if not config.enabled:
            return
        plex = plex or PlexService.from_db(db)
        if not plex:
            return
        rating_key = inventory.plex_rating_key
        if config.apply_cold_label and rating_key and config.cold_label:
            try:
                plex.set_label(rating_key, config.cold_label)
            except PlexServiceError:
                logger.exception("Failed to set cold label on %s", rating_key)
        if config.refresh_on_freeze:
            PlexPolicy._refresh_for_inventory(db, inventory, plex, config)

    @staticmethod
    def after_thaw(
        db: Session,
        inventory: FileInventory,
        *,
        config: PlexIntegrationConfig | None = None,
        plex: PlexService | None = None,
    ) -> None:
        config = config or get_or_create_plex_config(db)
        if not config.enabled:
            return
        plex = plex or PlexService.from_db(db)
        if not plex:
            return
        if config.refresh_on_thaw and inventory.plex_rating_key:
            try:
                plex.refresh_item(inventory.plex_rating_key)
            except PlexServiceError:
                logger.exception("Failed item refresh for %s", inventory.plex_rating_key)
                PlexPolicy._refresh_for_inventory(db, inventory, plex, config)

    @staticmethod
    def _refresh_for_inventory(
        db: Session,
        inventory: FileInventory,
        plex: PlexService,
        config: PlexIntegrationConfig,
    ) -> None:
        section_ids = {
            str(m.get("section_id"))
            for m in config.get_library_maps()
            if m.get("path_id") == inventory.path_id and m.get("section_id") is not None
        }
        if inventory.plex_rating_key:
            try:
                plex.refresh_item(inventory.plex_rating_key)
                return
            except PlexServiceError:
                logger.debug("item refresh failed; trying section refresh")
        for section_id in section_ids:
            try:
                plex.refresh_section(section_id)
            except PlexServiceError:
                logger.exception("section refresh failed for %s", section_id)

    @staticmethod
    def sessions_block_freeze(db: Session, file_path: str) -> bool:
        """True if PMS reports an active session for this file or playback lock is held."""
        if PlexPolicy.is_path_playback_locked(file_path):
            return True
        plex = PlexService.from_db(db)
        if not plex:
            return False
        try:
            for session in plex.list_sessions():
                for media in session.get("Media") or []:
                    for part in media.get("Part") or []:
                        part_file = part.get("file")
                        if not part_file:
                            continue
                        mapped = plex.map_plex_path_to_fridge(part_file)
                        if file_path in (part_file, mapped):
                            return True
                        rk = session.get("ratingKey")
                        if rk and PlexPolicy.is_playback_locked(f"rk:{rk}"):
                            return True
        except PlexServiceError:
            logger.debug("Could not list Plex sessions for freeze guard")
        return False

    @staticmethod
    def prethaw_on_deck(db: Session) -> dict[str, Any]:
        config = get_or_create_plex_config(db)
        if not config.enabled or not config.prethaw_on_deck:
            return {"thawed": [], "skipped": True}
        plex = PlexService.from_db(db)
        if not plex:
            return {"thawed": [], "skipped": True}
        thawed: list[int] = []
        try:
            items = plex.get_continue_watching()
        except PlexServiceError as exc:
            return {"thawed": [], "error": str(exc)}
        for item in items:
            rk = item.get("ratingKey")
            if not rk:
                continue
            try:
                parts = plex.get_item_parts(rk)
            except PlexServiceError:
                continue
            for part in parts:
                file_path = part.get("file")
                if not file_path:
                    continue
                inventory = PlexPolicy.find_inventory_for_plex_path(db, file_path, plex)
                if not inventory or inventory.storage_type != StorageType.COLD:
                    continue
                ok, _ = PlexPolicy.thaw_inventory(db, inventory, initiated_by="plex-on-deck")
                if ok:
                    thawed.append(inventory.id)
                    PlexPolicy.after_thaw(db, inventory, config=config, plex=plex)
        return {"thawed": thawed}

    @staticmethod
    def sync_watch_state(db: Session) -> dict[str, Any]:
        config = get_or_create_plex_config(db)
        if not config.enabled or not config.poll_watch_state:
            return {"updated": 0, "skipped": True}
        plex = PlexService.from_db(db)
        if not plex:
            return {"updated": 0, "skipped": True}
        updated = 0
        for mapping in config.get_library_maps():
            section_id = mapping.get("section_id")
            if section_id is None:
                continue
            try:
                parts = plex.poll_section_watch_state(section_id)
            except PlexServiceError:
                logger.exception("watch-state poll failed for section %s", section_id)
                continue
            for part in parts:
                file_path = part.get("file")
                if not file_path:
                    continue
                inventory = PlexPolicy.find_inventory_for_plex_path(db, file_path, plex)
                if not inventory:
                    continue
                viewed = PlexService.plex_epoch_to_datetime(part.get("last_viewed_at"))
                if viewed and (
                    inventory.last_plex_played_at is None or viewed > inventory.last_plex_played_at
                ):
                    inventory.last_plex_played_at = viewed
                    inventory.plex_rating_key = str(
                        part.get("rating_key") or inventory.plex_rating_key
                    )
                    if part.get("view_count") is not None:
                        inventory.plex_play_count = int(part["view_count"])
                    db.add(inventory)
                    updated += 1
        if updated:
            db.commit()
        return {"updated": updated}
