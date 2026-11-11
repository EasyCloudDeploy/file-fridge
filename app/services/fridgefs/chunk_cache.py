"""LRU chunk cache for FridgeFS ranged cold reads."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)


class ChunkCache:
    """Simple on-disk LRU cache keyed by (path, offset, size)."""

    def __init__(self, cache_dir: Path, max_bytes: int):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(int(max_bytes), 1)
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, int] = OrderedDict()
        self._used = 0
        self._hydrate_index()

    def _hydrate_index(self) -> None:
        for path in self.cache_dir.glob("*.chunk"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            self._entries[path.name] = size
            self._used += size

    @staticmethod
    def _key(file_key: str, offset: int, size: int) -> str:
        digest = hashlib.sha256(f"{file_key}:{offset}:{size}".encode()).hexdigest()
        return f"{digest}.chunk"

    def get(self, file_key: str, offset: int, size: int) -> bytes | None:
        name = self._key(file_key, offset, size)
        path = self.cache_dir / name
        with self._lock:
            if name not in self._entries or not path.exists():
                return None
            self._entries.move_to_end(name)
            try:
                return path.read_bytes()
            except OSError:
                return None

    def put(self, file_key: str, offset: int, size: int, data: bytes) -> None:
        name = self._key(file_key, offset, size)
        path = self.cache_dir / name
        with self._lock:
            try:
                path.write_bytes(data)
            except OSError:
                logger.exception("Failed writing cache chunk %s", path)
                return
            prev = self._entries.pop(name, 0)
            self._used -= prev
            self._entries[name] = len(data)
            self._used += len(data)
            self._evict()

    def _evict(self) -> None:
        while self._used > self.max_bytes and self._entries:
            name, size = self._entries.popitem(last=False)
            self._used -= size
            with contextlib.suppress(OSError):
                (self.cache_dir / name).unlink(missing_ok=True)

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._entries),
                "used_bytes": self._used,
                "max_bytes": self.max_bytes,
                "cache_dir": str(self.cache_dir),
                "updated_at": time.time(),
            }
