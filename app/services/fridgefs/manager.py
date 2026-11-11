"""FridgeFS mount lifecycle manager."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import MonitoredPath, PlexIntegrationConfig
from app.services.fridgefs.chunk_cache import ChunkCache

logger = logging.getLogger(__name__)

try:
    import pyfuse3
    import trio

    HAS_FUSE = True
except ImportError:  # pragma: no cover
    pyfuse3 = None  # type: ignore
    trio = None  # type: ignore
    HAS_FUSE = False


class FridgeFSManager:
    """Supervises a single FridgeFS mount for this process."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._mounted = False
        self._mount_path: Path | None = None
        self._cache: ChunkCache | None = None
        self._stop = threading.Event()
        self._ops = None

    def fuse_available(self) -> bool:
        return HAS_FUSE and Path("/dev/fuse").exists()

    def is_mounted(self) -> bool:
        return self._mounted

    def cache_stats(self) -> dict[str, Any]:
        if self._cache:
            return self._cache.stats()
        return {"entries": 0, "used_bytes": 0, "max_bytes": 0}

    def mount(self, db: Session, config: PlexIntegrationConfig) -> None:
        if self._mounted:
            return
        if not HAS_FUSE:
            msg = (
                "pyfuse3 is not installed. Install the optional FridgeFS dependency "
                "and ensure fuse3 is available on the host."
            )
            raise RuntimeError(
                msg
            )
        if not Path("/dev/fuse").exists():
            msg = "/dev/fuse is not available in this environment"
            raise RuntimeError(msg)

        mount_path = Path(config.fridgefs_mount_path or "/mnt/fridgefs")
        mount_path.mkdir(parents=True, exist_ok=True)
        self._recover_stale_mount(mount_path)

        roots = self._resolve_roots(db, config)
        if not roots:
            # Fall back to all enabled monitored paths
            roots = [
                Path(p.source_path)
                for p in db.query(MonitoredPath).filter(MonitoredPath.enabled.is_(True)).all()
            ]
        if not roots:
            msg = "No monitored paths available to expose via FridgeFS"
            raise RuntimeError(msg)

        cache_dir = Path(
            config.fridgefs_cache_dir or str(Path(settings.database_path).parent / "fridgefs-cache")
        )
        max_bytes = int(config.fridgefs_cache_max_gb or 20) * 1024 * 1024 * 1024
        self._cache = ChunkCache(cache_dir, max_bytes)

        from app.services.fridgefs.ops import FridgeFSOps

        self._ops = FridgeFSOps(
            roots,
            db_factory=SessionLocal,
            cache=self._cache,
            thaw_on_open=True,
        )
        self._mount_path = mount_path
        self._stop.clear()

        allow_other = bool(config.fridgefs_allow_other)
        self._thread = threading.Thread(
            target=self._run_fuse,
            args=(mount_path, allow_other),
            name="fridgefs-fuse",
            daemon=True,
        )
        self._thread.start()
        # Mark mounted once thread starts; ops loop will clear on failure
        self._mounted = True
        logger.info("FridgeFS mounting at %s (roots=%s)", mount_path, roots)

    def _resolve_roots(self, db: Session, config: PlexIntegrationConfig) -> list[Path]:
        roots: list[Path] = []
        path_ids = {
            int(m["path_id"])
            for m in config.get_library_maps()
            if m.get("mode") == "fridgefs" and m.get("path_id") is not None
        }
        if not path_ids:
            return roots
        for path in db.query(MonitoredPath).filter(MonitoredPath.id.in_(path_ids)).all():
            roots.append(Path(path.source_path))
        return roots

    def _run_fuse(self, mount_path: Path, allow_other: bool) -> None:
        assert HAS_FUSE
        assert self._ops is not None
        fuse_options = set(pyfuse3.default_options)
        fuse_options.add("fsname=fridgefs")
        if allow_other:
            fuse_options.add("allow_other")
        try:
            pyfuse3.init(self._ops, str(mount_path), fuse_options)
            trio.run(pyfuse3.main)
        except Exception:
            logger.exception("FridgeFS fuse loop exited with error")
            self._mounted = False
        finally:
            try:
                pyfuse3.close(unmount=True)
            except Exception:
                logger.debug("pyfuse3.close failed", exc_info=True)
            self._mounted = False

    def unmount(self) -> None:
        if self._mount_path:
            self._recover_stale_mount(self._mount_path)
        self._mounted = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    @staticmethod
    def _recover_stale_mount(mount_path: Path) -> None:
        if not mount_path.exists():
            return
        fusermount = shutil.which("fusermount3") or shutil.which("fusermount")
        if not fusermount:
            return
        try:
            subprocess.run(
                [fusermount, "-u", str(mount_path)],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except Exception:
            logger.debug("fusermount cleanup failed for %s", mount_path, exc_info=True)


fridgefs_manager = FridgeFSManager()
