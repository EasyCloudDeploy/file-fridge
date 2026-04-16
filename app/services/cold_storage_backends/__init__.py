"""Cold storage backend modules."""

from app.services.cold_storage_backends.base import ColdStorageCapabilities
from app.services.cold_storage_backends.factory import get_backend

__all__ = ["ColdStorageCapabilities", "get_backend"]
