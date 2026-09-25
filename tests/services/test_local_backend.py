"""Tests for local filesystem cold storage backend."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models import ColdStorageLocation, OperationType
from app.services.cold_storage_backends.local_backend import LocalColdStorageBackend


@pytest.mark.unit
class TestLocalColdStorageBackend:
    @pytest.fixture
    def backend(self) -> LocalColdStorageBackend:
        return LocalColdStorageBackend()

    @pytest.fixture
    def location(self, tmp_path: Path) -> ColdStorageLocation:
        cold_root = tmp_path / "cold"
        cold_root.mkdir()
        return ColdStorageLocation(
            id=10,
            name="Local Cold",
            path=str(cold_root),
            operation_mode=OperationType.MOVE,
        )

    def test_backend_name_and_capabilities(self, backend: LocalColdStorageBackend) -> None:
        assert backend.backend_name() == "local"
        caps = backend.capabilities()
        assert caps.supports_symlink is True
        assert caps.supports_local_path_stats is True

    def test_validate_location_missing(self, backend: LocalColdStorageBackend) -> None:
        loc = ColdStorageLocation(name="missing", path="/nonexistent/path/xyz")
        ok, err = backend.validate_location(loc)
        assert ok is False
        assert "not found" in (err or "").lower()

    def test_validate_location_not_directory(
        self, backend: LocalColdStorageBackend, tmp_path: Path
    ) -> None:
        file_path = tmp_path / "file.txt"
        file_path.write_text("x")
        loc = ColdStorageLocation(name="file", path=str(file_path))
        ok, err = backend.validate_location(loc)
        assert ok is False
        assert "not a directory" in (err or "").lower()

    def test_validate_location_success(self, backend: LocalColdStorageBackend, location) -> None:
        ok, err = backend.validate_location(location)
        assert ok is True
        assert err is None

    def test_build_reference(self, backend: LocalColdStorageBackend, location) -> None:
        ref = backend.build_reference(location, Path("a/b.txt"))
        assert "a/b.txt" in ref.replace("\\", "/")

    def test_freeze_destination_exists(
        self, backend: LocalColdStorageBackend, location, tmp_path: Path
    ) -> None:
        source = tmp_path / "hot.bin"
        source.write_bytes(b"data")
        existing = Path(location.path) / "dup.bin"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"exists")

        ok, err, ref, checksum = backend.freeze_file(
            source, Path("dup.bin"), location, OperationType.MOVE
        )
        assert ok is False
        assert "already exists" in (err or "").lower()
        assert ref is None
        assert checksum is None

    def test_freeze_move_success(
        self,
        backend: LocalColdStorageBackend,
        location,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "hot.bin"
        source.write_bytes(b"move-me")

        monkeypatch.setattr(
            "app.services.cold_storage_backends.local_backend.move_with_rollback",
            lambda *_a, **_k: (True, None, "checksum-abc"),
        )

        ok, err, ref, checksum = backend.freeze_file(
            source, Path("nested/file.bin"), location, OperationType.MOVE
        )
        assert ok is True
        assert err is None
        assert ref is not None
        assert checksum == "checksum-abc"

    def test_freeze_move_failure(
        self,
        backend: LocalColdStorageBackend,
        location,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "hot.bin"
        source.write_bytes(b"x")
        monkeypatch.setattr(
            "app.services.cold_storage_backends.local_backend.move_with_rollback",
            lambda *_a, **_k: (False, "move failed", None),
        )

        ok, err, ref, checksum = backend.freeze_file(
            source, Path("f.bin"), location, OperationType.MOVE
        )
        assert ok is False
        assert err == "move failed"
        assert ref is None

    def test_thaw_missing_source(
        self, backend: LocalColdStorageBackend, location, tmp_path: Path
    ) -> None:
        ok, err = backend.thaw_file(
            "/nonexistent/cold/file.bin",
            tmp_path / "dest.bin",
            location,
            OperationType.MOVE,
        )
        assert ok is False
        assert "not found" in (err or "").lower()

    def test_thaw_copy_when_destination_exists(
        self, backend: LocalColdStorageBackend, location, tmp_path: Path
    ) -> None:
        cold_file = Path(location.path) / "cold.bin"
        cold_file.parent.mkdir(parents=True, exist_ok=True)
        cold_file.write_bytes(b"cold")
        dest = tmp_path / "dest.bin"
        dest.write_bytes(b"hot")

        ok, err = backend.thaw_file(str(cold_file), dest, location, OperationType.COPY)
        assert ok is True
        assert err is None
        assert not cold_file.exists()

    def test_thaw_move_success(
        self,
        backend: LocalColdStorageBackend,
        location,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cold_file = Path(location.path) / "restore.bin"
        cold_file.write_bytes(b"restore")
        dest = tmp_path / "hot" / "restore.bin"

        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._move_preserving_timestamps",
            lambda src, dst: (dst, MagicMock()),
        )

        ok, err = backend.thaw_file(str(cold_file), dest, location, OperationType.MOVE)
        assert ok is True
        assert err is None
        assert not cold_file.exists()

    def test_thaw_symlink_removes_existing_symlink(
        self,
        backend: LocalColdStorageBackend,
        location,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cold_file = Path(location.path) / "link-target.bin"
        cold_file.write_bytes(b"payload")
        dest = tmp_path / "symlink.bin"
        dest.symlink_to("/tmp/other")

        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._move_preserving_timestamps",
            lambda src, dst: (dst, MagicMock()),
        )

        ok, err = backend.thaw_file(str(cold_file), dest, location, OperationType.SYMLINK)
        assert ok is True
        assert err is None

    def test_thaw_staged_finalize(
        self,
        backend: LocalColdStorageBackend,
        location,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cold_file = Path(location.path) / "staged.bin"
        cold_file.write_bytes(b"staged")
        dest = tmp_path / "final.bin"
        staged = tmp_path / "staged.tmp"

        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._move_preserving_timestamps",
            lambda src, dst: (staged, MagicMock()),
        )
        finalize = MagicMock()
        monkeypatch.setattr(
            "app.services.file_thawer.FileThawer._finalize_staged_move",
            finalize,
        )

        ok, err = backend.thaw_file(str(cold_file), dest, location, OperationType.MOVE)
        assert ok is True
        finalize.assert_called_once()

    def test_exists_and_delete(
        self, backend: LocalColdStorageBackend, location, tmp_path: Path
    ) -> None:
        cold_file = Path(location.path) / "delete-me.bin"
        cold_file.write_bytes(b"x")

        assert backend.exists(str(cold_file), location) is True
        ok, err = backend.delete(str(cold_file), location)
        assert ok is True
        assert err is None
        assert not cold_file.exists()

    def test_delete_missing_is_success(self, backend: LocalColdStorageBackend, location) -> None:
        ok, err = backend.delete(str(Path(location.path) / "missing.bin"), location)
        assert ok is True
        assert err is None

    def test_delete_failure(
        self,
        backend: LocalColdStorageBackend,
        location,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cold_file = Path(location.path) / "locked.bin"
        cold_file.write_bytes(b"x")

        def fail_unlink(self, missing_ok=False):
            raise PermissionError("locked")

        monkeypatch.setattr(Path, "unlink", fail_unlink)

        ok, err = backend.delete(str(cold_file), location)
        assert ok is False
        assert err is not None
