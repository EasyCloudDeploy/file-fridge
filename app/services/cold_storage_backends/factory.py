"""Factory for cold storage backend modules."""

from app.models import ColdStorageBackendType, ColdStorageLocation
from app.services.cold_storage_backends.gdrive_backend import GoogleDriveColdStorageBackend
from app.services.cold_storage_backends.local_backend import LocalColdStorageBackend
from app.services.cold_storage_backends.s3_backend import S3ColdStorageBackend


_BACKENDS = {
    ColdStorageBackendType.LOCAL: LocalColdStorageBackend(),
    ColdStorageBackendType.S3: S3ColdStorageBackend(),
    ColdStorageBackendType.GDRIVE: GoogleDriveColdStorageBackend(),
}


def get_backend(location: ColdStorageLocation):
    backend = _BACKENDS.get(location.backend_type)
    if backend is None:
        raise ValueError(f"Unsupported cold storage backend type: {location.backend_type}")
    return backend
