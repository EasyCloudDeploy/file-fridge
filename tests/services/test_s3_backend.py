"""Tests for S3 cold storage backend."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models import ColdStorageBackendType, ColdStorageLocation, OperationType
from app.services.cold_storage_backends.s3_backend import S3ColdStorageBackend


@pytest.mark.unit
class TestS3ColdStorageBackend:
    @pytest.fixture
    def location(self) -> ColdStorageLocation:
        loc = ColdStorageLocation(
            id=1,
            name="S3 Cold",
            path="s3://my-bucket/prefix",
            backend_type=ColdStorageBackendType.S3,
            operation_mode=OperationType.MOVE,
        )
        loc.set_backend_config(
            {
                "bucket": "my-bucket",
                "prefix": "cold/prefix",
                "region": "us-east-1",
                "access_key_id": "AKIA",
                "secret_access_key": "secret",
            }
        )
        return loc

    @pytest.fixture
    def backend(self) -> S3ColdStorageBackend:
        return S3ColdStorageBackend()

    def test_backend_name_and_capabilities(self, backend: S3ColdStorageBackend) -> None:
        assert backend.backend_name() == "s3"
        caps = backend.capabilities()
        assert caps.supports_move is True
        assert caps.supports_symlink is False
        assert caps.supports_local_path_stats is False

    def test_client_requires_boto3(
        self,
        backend: S3ColdStorageBackend,
        location: ColdStorageLocation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("no boto3")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(RuntimeError, match="boto3 is required"):
            backend._client(location)

    def test_bucket_prefix_missing_bucket(self, backend: S3ColdStorageBackend) -> None:
        loc = ColdStorageLocation(name="bad", path="s3://x", backend_type=ColdStorageBackendType.S3)
        loc.set_backend_config({})
        with pytest.raises(RuntimeError, match="Missing S3 backend config: 'bucket'"):
            backend._bucket_prefix(loc)

    def test_key_for_relative_with_prefix(self, backend: S3ColdStorageBackend, location) -> None:
        key = backend._key_for_relative(location, Path("folder/file.txt"))
        assert key == "cold/prefix/folder/file.txt"

    def test_key_for_relative_empty_relative(self, backend: S3ColdStorageBackend, location) -> None:
        key = backend._key_for_relative(location, Path())
        assert key in {"cold/prefix", "cold/prefix/."}

    def test_parse_reference_valid(self, backend: S3ColdStorageBackend) -> None:
        bucket, key = backend._parse_reference("s3://my-bucket/path/to/file.txt")
        assert bucket == "my-bucket"
        assert key == "path/to/file.txt"

    def test_parse_reference_invalid(self, backend: S3ColdStorageBackend) -> None:
        with pytest.raises(RuntimeError, match="Invalid S3 storage reference"):
            backend._parse_reference("file:///local/path")

    def test_validate_location_success(self, backend: S3ColdStorageBackend, location) -> None:
        ok, err = backend.validate_location(location)
        assert ok is True
        assert err is None

    def test_validate_location_empty_bucket(self, backend: S3ColdStorageBackend) -> None:
        loc = ColdStorageLocation(name="bad", path="s3://", backend_type=ColdStorageBackendType.S3)
        loc.set_backend_config({"bucket": "   "})
        ok, err = backend.validate_location(loc)
        assert ok is False
        assert "bucket" in (err or "").lower()

    def test_build_reference(self, backend: S3ColdStorageBackend, location) -> None:
        ref = backend.build_reference(location, Path("a/b.txt"))
        assert ref.startswith("s3://my-bucket/")
        assert "cold%2Fprefix%2Fa%2Fb.txt" in ref or "cold/prefix/a/b.txt" in ref

    def test_freeze_symlink_unsupported(
        self, backend: S3ColdStorageBackend, location, tmp_path
    ) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        ok, err, ref, checksum = backend.freeze_file(
            src, Path("rel.bin"), location, OperationType.SYMLINK
        )
        assert ok is False
        assert "symlink" in (err or "").lower()
        assert ref is None

    def test_freeze_move_success(
        self, backend: S3ColdStorageBackend, location, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"payload")

        mock_client = MagicMock()
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        ok, err, ref, checksum = backend.freeze_file(
            src, Path("nested/file.bin"), location, OperationType.MOVE
        )
        assert ok is True
        assert err is None
        assert ref is not None and ref.startswith("s3://my-bucket/")
        assert checksum is not None
        mock_client.upload_file.assert_called_once()
        assert not src.exists()

    def test_freeze_copy_keeps_source(
        self, backend: S3ColdStorageBackend, location, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"keep")

        mock_client = MagicMock()
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        ok, _, _, _ = backend.freeze_file(src, Path("file.bin"), location, OperationType.COPY)
        assert ok is True
        assert src.exists()

    def test_freeze_failure(
        self, backend: S3ColdStorageBackend, location, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")

        mock_client = MagicMock()
        mock_client.upload_file.side_effect = RuntimeError("upload failed")
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        ok, err, ref, checksum = backend.freeze_file(
            src, Path("f.bin"), location, OperationType.COPY
        )
        assert ok is False
        assert "S3 freeze failed" in (err or "")
        assert ref is None
        assert checksum is None

    def test_thaw_symlink_unsupported(
        self, backend: S3ColdStorageBackend, location, tmp_path
    ) -> None:
        ok, err = backend.thaw_file(
            "s3://my-bucket/key", tmp_path / "out.bin", location, OperationType.SYMLINK
        )
        assert ok is False
        assert "symlink" in (err or "").lower()

    def test_thaw_move_success(
        self, backend: S3ColdStorageBackend, location, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "restored.bin"
        mock_client = MagicMock()

        def write_download(_bucket, _key, filename: str) -> None:
            Path(filename).write_bytes(b"restored")

        mock_client.download_file.side_effect = write_download
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        ok, err = backend.thaw_file(
            "s3://my-bucket/cold/prefix/file.bin", dest, location, OperationType.MOVE
        )
        assert ok is True
        assert err is None
        mock_client.download_file.assert_called_once()
        mock_client.delete_object.assert_called_once()

    def test_thaw_copy_when_destination_exists(
        self, backend: S3ColdStorageBackend, location, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "existing.bin"
        dest.write_bytes(b"already here")
        mock_client = MagicMock()
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        ok, err = backend.thaw_file("s3://my-bucket/key", dest, location, OperationType.COPY)
        assert ok is True
        assert err is None
        mock_client.delete_object.assert_called_once()
        mock_client.download_file.assert_not_called()

    def test_exists_true_and_false(
        self, backend: S3ColdStorageBackend, location, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_client = MagicMock()
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        assert backend.exists("s3://my-bucket/key", location) is True

        mock_client.head_object.side_effect = RuntimeError("not found")
        assert backend.exists("s3://my-bucket/missing", location) is False

    def test_delete_success_and_failure(
        self, backend: S3ColdStorageBackend, location, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_client = MagicMock()
        monkeypatch.setattr(backend, "_client", lambda _loc: mock_client)

        ok, err = backend.delete("s3://my-bucket/key", location)
        assert ok is True
        assert err is None

        mock_client.delete_object.side_effect = RuntimeError("denied")
        ok, err = backend.delete("s3://my-bucket/key", location)
        assert ok is False
        assert err is not None
