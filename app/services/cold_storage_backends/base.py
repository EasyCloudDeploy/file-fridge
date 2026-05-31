"""Base interfaces for cold storage backend modules."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol, Tuple

from app.models import ColdStorageLocation, OperationType


@dataclass(frozen=True)
class ColdStorageCapabilities:
    """Supported backend operations and feature flags."""

    supports_move: bool = True
    supports_copy: bool = True
    supports_symlink: bool = False
    supports_local_path_stats: bool = False


class ColdStorageBackend(Protocol):
    """Protocol for backend implementations."""

    def backend_name(self) -> str: ...

    def capabilities(self) -> ColdStorageCapabilities: ...

    def validate_location(self, location: ColdStorageLocation) -> Tuple[bool, Optional[str]]: ...

    def build_reference(self, location: ColdStorageLocation, relative_path: Path) -> str: ...

    def freeze_file(
        self,
        source_path: Path,
        relative_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Freeze from hot storage into backend.

        Returns (success, error, storage_reference, checksum_after).
        """
        ...

    def thaw_file(
        self,
        storage_reference: str,
        destination_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
    ) -> Tuple[bool, Optional[str]]:
        """Thaw from backend to hot storage.

        Returns (success, error).
        """
        ...

    def exists(self, storage_reference: str, location: ColdStorageLocation) -> bool: ...

    def delete(
        self, storage_reference: str, location: ColdStorageLocation
    ) -> Tuple[bool, Optional[str]]: ...

    def download_file(
        self,
        storage_reference: str,
        destination_path: Path,
        location: ColdStorageLocation,
    ) -> Tuple[bool, Optional[str]]:
        """Download a file to a local path without removing it from the backend.

        Returns (success, error).
        """
        ...
