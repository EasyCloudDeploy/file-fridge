"""FridgeFS FUSE operations: hot pass-through + cold thaw-on-open / ranged reads."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import pyfuse3
    from pyfuse3 import FUSEError

    HAS_PYFUSE3 = True
except ImportError:  # pragma: no cover - optional dependency
    pyfuse3 = None  # type: ignore
    HAS_PYFUSE3 = False

    class FUSEError(OSError):  # type: ignore
        def __init__(self, errcode: int):
            super().__init__(errcode)
            self.errno = errcode


def _base_ops():
    if HAS_PYFUSE3:
        return pyfuse3.Operations
    return object


class FridgeFSOps(_base_ops()):
    """
    Virtual filesystem operations.

    Root presents configured source roots (monitored hot paths). Cold files
    trigger priority thaw and can serve ranged reads from cold while thawing.
    """

    def __init__(
        self,
        roots: list[Path],
        *,
        db_factory,
        cache,
        thaw_on_open: bool = True,
    ):
        if HAS_PYFUSE3:
            super().__init__()
        self.roots = [Path(r).resolve() for r in roots]
        self.db_factory = db_factory
        self.cache = cache
        self.thaw_on_open = thaw_on_open
        self._inode_to_path: dict[int, Path] = {pyfuse3.ROOT_INODE if HAS_PYFUSE3 else 1: Path("/")}
        self._path_to_inode: dict[str, int] = {}
        self._next_inode = (pyfuse3.ROOT_INODE if HAS_PYFUSE3 else 1) + 1
        self._fh_to_handle: dict[int, dict[str, Any]] = {}
        self._next_fh = 1
        self._lock = threading.Lock()
        # Seed virtual top-level dirs for each root
        for idx, root in enumerate(self.roots):
            name = root.name or f"root{idx}"
            inode = self._alloc_inode(Path("/") / name)
            self._inode_to_path[inode] = root

    def _alloc_inode(self, path: Path) -> int:
        key = str(path)
        with self._lock:
            if key in self._path_to_inode:
                return self._path_to_inode[key]
            inode = self._next_inode
            self._next_inode += 1
            self._path_to_inode[key] = inode
            self._inode_to_path[inode] = path
            return inode

    def _resolve(self, inode: int) -> Path:
        path = self._inode_to_path.get(inode)
        if path is None:
            raise FUSEError(errno.ENOENT)
        return path

    def _lookup_child(self, parent: Path, name: str) -> Path:
        if parent == Path("/"):
            for root in self.roots:
                if root.name == name:
                    return root
            raise FUSEError(errno.ENOENT)
        candidate = parent / name
        if candidate.exists() or self._inventory_exists(str(candidate)):
            return candidate
        raise FUSEError(errno.ENOENT)

    def _inventory_exists(self, file_path: str) -> bool:
        from app.models import FileInventory

        db = self.db_factory()
        try:
            return (
                db.query(FileInventory.id).filter(FileInventory.file_path == file_path).first()
                is not None
            )
        finally:
            db.close()

    def _inventory_entry(self, file_path: str):
        from app.models import FileInventory

        db = self.db_factory()
        try:
            return db.query(FileInventory).filter(FileInventory.file_path == file_path).first()
        finally:
            db.close()

    def getattr(self, inode: int, ctx=None):  # noqa: ARG002
        path = self._resolve(inode)
        return self._stat_for_path(path)

    def _stat_for_path(self, path: Path):
        entry = pyfuse3.EntryAttributes() if HAS_PYFUSE3 else type("EntryAttributes", (), {})()

        if path == Path("/"):
            mode = stat.S_IFDIR | 0o555
            size = 0
            mtime = time_ns = int(datetime.now(UTC).timestamp() * 1e9)
        elif path in self.roots or path.is_dir():
            try:
                st = path.stat()
                mode = stat.S_IFDIR | (st.st_mode & 0o777)
                size = st.st_size
                time_ns = int(st.st_mtime_ns)
            except OSError:
                mode = stat.S_IFDIR | 0o555
                size = 0
                time_ns = int(datetime.now(UTC).timestamp() * 1e9)
        else:
            try:
                st = path.stat()
                mode = stat.S_IFREG | (st.st_mode & 0o777)
                size = st.st_size
                time_ns = int(st.st_mtime_ns)
            except OSError:
                inv = self._inventory_entry(str(path))
                if not inv:
                    raise FUSEError(errno.ENOENT) from None
                mode = stat.S_IFREG | 0o444
                size = int(inv.file_size or 0)
                mtime = inv.file_mtime or datetime.now(UTC)
                if mtime.tzinfo is None:
                    mtime = mtime.replace(tzinfo=UTC)
                time_ns = int(mtime.timestamp() * 1e9)

        entry.st_mode = mode
        entry.st_size = size
        entry.st_nlink = 1
        entry.st_uid = os.getuid()
        entry.st_gid = os.getgid()
        entry.st_ino = self._alloc_inode(path if path != Path("/") else Path("/"))
        entry.st_atime_ns = time_ns
        entry.st_mtime_ns = time_ns
        entry.st_ctime_ns = time_ns
        entry.entry_timeout = 1
        entry.attr_timeout = 1
        return entry

    def lookup(self, parent_inode: int, name: bytes, ctx=None):  # noqa: ARG002
        parent = self._resolve(parent_inode)
        child = self._lookup_child(parent, os.fsdecode(name))
        inode = self._alloc_inode(child)
        self._inode_to_path[inode] = child
        return self._stat_for_path(child)

    def opendir(self, inode: int, ctx=None):  # noqa: ARG002
        return inode

    def readdir(self, inode: int, start_id: int, token):
        path = self._resolve(inode)
        entries: list[tuple[str, Path]] = []
        if path == Path("/"):
            for root in self.roots:
                entries.append((root.name, root))
        else:
            try:
                for child in sorted(path.iterdir()):
                    entries.append((child.name, child))
            except OSError:
                pass
            # Merge inventory-only cold files under this directory
            from app.models import FileInventory

            db = self.db_factory()
            try:
                prefix = str(path).rstrip("/") + "/"
                for inv in (
                    db.query(FileInventory)
                    .filter(FileInventory.file_path.startswith(prefix))
                    .limit(5000)
                    .all()
                ):
                    rel = inv.file_path[len(prefix) :]
                    if "/" in rel or not rel:
                        continue
                    if any(name == rel for name, _ in entries):
                        continue
                    entries.append((rel, Path(inv.file_path)))
            finally:
                db.close()

        for idx, (name, child_path) in enumerate(entries):
            if idx < start_id:
                continue
            child_inode = self._alloc_inode(child_path)
            self._inode_to_path[child_inode] = child_path
            if HAS_PYFUSE3:
                attr = self._stat_for_path(child_path)
                if not pyfuse3.readdir_reply(token, os.fsencode(name), attr, idx + 1):
                    break
            else:
                yield name, child_path, idx + 1

    def open(self, inode: int, flags: int, ctx=None):  # noqa: ARG002
        path = self._resolve(inode)
        handle: dict[str, Any] = {"path": path, "fd": None, "cold": False, "inventory_id": None}

        if path.exists() and path.is_file():
            fd = os.open(path, os.O_RDONLY)
            handle["fd"] = fd
        else:
            inv = self._inventory_entry(str(path))
            if not inv:
                raise FUSEError(errno.ENOENT)
            handle["cold"] = True
            handle["inventory_id"] = inv.id
            if self.thaw_on_open:
                self._start_thaw(inv.id)
            # Prefer opening cold bytes for ranged reads if local cold path exists
            cold_fd = self._open_cold_fd(inv)
            if cold_fd is not None:
                handle["fd"] = cold_fd
            self._touch_plex_stats(str(path))

        with self._lock:
            fh = self._next_fh
            self._next_fh += 1
            self._fh_to_handle[fh] = handle
        if HAS_PYFUSE3:
            return pyfuse3.FileInfo(fh=fh, direct_io=False, keep_cache=True)
        return fh

    def _open_cold_fd(self, inv) -> int | None:
        from app.models import FileRecord

        db = self.db_factory()
        try:
            record = (
                db.query(FileRecord)
                .filter(
                    (FileRecord.cold_storage_path == inv.file_path)
                    | (FileRecord.original_path == inv.file_path)
                )
                .first()
            )
            if not record or not record.cold_storage_path:
                return None
            cold = Path(record.cold_storage_path)
            if cold.exists() and cold.is_file():
                return os.open(cold, os.O_RDONLY)
        finally:
            db.close()
        return None

    def _start_thaw(self, inventory_id: int) -> None:
        def _worker():
            from app.models import FileInventory
            from app.services.plex_policy import PlexPolicy

            db = self.db_factory()
            try:
                inv = db.query(FileInventory).filter(FileInventory.id == inventory_id).first()
                if inv:
                    PlexPolicy.thaw_inventory(db, inv, initiated_by="fridgefs-open")
            except Exception:
                logger.exception("FridgeFS thaw failed for inventory %s", inventory_id)
            finally:
                db.close()

        threading.Thread(target=_worker, name=f"fridgefs-thaw-{inventory_id}", daemon=True).start()

    def _touch_plex_stats(self, file_path: str) -> None:
        from app.models import FileInventory

        db = self.db_factory()
        try:
            inv = db.query(FileInventory).filter(FileInventory.file_path == file_path).first()
            if inv:
                inv.last_plex_played_at = datetime.now(UTC)
                db.add(inv)
                db.commit()
        except Exception:
            logger.debug("Failed updating plex stats for %s", file_path, exc_info=True)
        finally:
            db.close()

    def read(self, fh: int, off: int, size: int):
        handle = self._fh_to_handle.get(fh)
        if not handle:
            raise FUSEError(errno.EBADF)
        path: Path = handle["path"]
        # Promote to hot fd if thaw completed
        if handle.get("cold") and path.exists() and path.is_file():
            try:
                if handle.get("fd") is not None:
                    os.close(handle["fd"])
            except OSError:
                pass
            handle["fd"] = os.open(path, os.O_RDONLY)
            handle["cold"] = False

        fd = handle.get("fd")
        if fd is not None:
            return os.pread(fd, size, off)

        # Cache-backed ranged read fallback
        cached = self.cache.get(str(path), off, size) if self.cache else None
        if cached is not None:
            return cached
        raise FUSEError(errno.EIO)

    def release(self, fh: int):
        handle = self._fh_to_handle.pop(fh, None)
        if not handle:
            return
        path = handle.get("path")
        if path:
            self._touch_plex_stats(str(path))
        fd = handle.get("fd")
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

    def releasedir(self, fh: int):  # noqa: ARG002
        return None
