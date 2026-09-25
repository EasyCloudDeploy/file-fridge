"""Tests for cold storage backend factory."""

import pytest

from app.models import ColdStorageBackendType, ColdStorageLocation
from app.services.cold_storage_backends.factory import get_backend
from app.services.cold_storage_backends.gdrive_backend import GoogleDriveColdStorageBackend
from app.services.cold_storage_backends.local_backend import LocalColdStorageBackend
from app.services.cold_storage_backends.s3_backend import S3ColdStorageBackend


@pytest.mark.unit
class TestColdStorageFactory:
    def test_get_local_backend(self) -> None:
        location = ColdStorageLocation(name="Local", path="/tmp/cold")
        location.backend_type = ColdStorageBackendType.LOCAL
        backend = get_backend(location)
        assert isinstance(backend, LocalColdStorageBackend)

    def test_get_s3_backend(self) -> None:
        location = ColdStorageLocation(name="S3", path="s3://bucket")
        location.backend_type = ColdStorageBackendType.S3
        backend = get_backend(location)
        assert isinstance(backend, S3ColdStorageBackend)

    def test_get_gdrive_backend(self) -> None:
        location = ColdStorageLocation(name="GDrive", path="gdrive://folder")
        location.backend_type = ColdStorageBackendType.GDRIVE
        backend = get_backend(location)
        assert isinstance(backend, GoogleDriveColdStorageBackend)

    def test_get_backend_returns_singleton_instances(self) -> None:
        loc1 = ColdStorageLocation(name="Local", path="/a")
        loc1.backend_type = ColdStorageBackendType.LOCAL
        loc2 = ColdStorageLocation(name="Local2", path="/b")
        loc2.backend_type = ColdStorageBackendType.LOCAL
        assert get_backend(loc1) is get_backend(loc2)

    def test_unsupported_backend_raises(self) -> None:
        location = ColdStorageLocation(name="Bad", path="/x")

        class _UnsupportedBackend:
            value = "unsupported"

        location.backend_type = _UnsupportedBackend()  # type: ignore[assignment]
        with pytest.raises(ValueError, match="Unsupported cold storage backend type"):
            get_backend(location)
