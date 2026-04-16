"""Local filesystem cold storage backend."""

from pathlib import Path
from typing import Optional, Tuple

from app.models import ColdStorageLocation, OperationType
from app.services.checksum_verifier import checksum_verifier
from app.services.cold_storage_backends.base import ColdStorageBackend, ColdStorageCapabilities
from app.services.file_mover import move_with_rollback


class LocalColdStorageBackend(ColdStorageBackend):
    def backend_name(self) -> str:
        return "local"

    def capabilities(self) -> ColdStorageCapabilities:
        return ColdStorageCapabilities(
            supports_move=True,
            supports_copy=True,
            supports_symlink=True,
            supports_local_path_stats=True,
        )

    def validate_location(self, location: ColdStorageLocation) -> Tuple[bool, Optional[str]]:
        root = Path(location.path)
        if not root.exists():
            return False, f"Path not found: {location.path}"
        if not root.is_dir():
            return False, f"Path is not a directory: {location.path}"
        return True, None

    def build_reference(self, location: ColdStorageLocation, relative_path: Path) -> str:
        return str(Path(location.path) / relative_path)

    def freeze_file(
        self,
        source_path: Path,
        relative_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        destination = Path(location.path) / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return False, f"Destination already exists: {destination}", None, None

        success, error, checksum_after = move_with_rollback(
            source_path,
            destination,
            operation_mode,
            verify_checksum=True,
        )
        if not success:
            return False, error, None, None
        return True, None, str(destination), checksum_after

    def thaw_file(
        self,
        storage_reference: str,
        destination_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
    ) -> Tuple[bool, Optional[str]]:
        source = Path(storage_reference)
        if not source.exists():
            return False, f"File not found in cold storage: {storage_reference}"

        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if operation_mode == OperationType.COPY and destination_path.exists():
            source.unlink()
            return True, None

        from app.services.file_thawer import FileThawer

        # For SYMLINK thaw, remove the symlink before moving the payload back.
        if operation_mode == OperationType.SYMLINK and destination_path.exists() and destination_path.is_symlink():
            destination_path.unlink()

        prepared_path, prepared_stat = FileThawer._move_preserving_timestamps(source, destination_path)
        if prepared_path != destination_path:
            FileThawer._finalize_staged_move(source, prepared_path, destination_path, prepared_stat)

        # Keep parity with old thaw behavior: copy keeps hot file; move/symlink remove cold data.
        if operation_mode in (OperationType.MOVE, OperationType.SYMLINK) and source.exists():
            source.unlink()

        if operation_mode == OperationType.COPY and source.exists():
            source.unlink()

        # Checksum side effect parity for local backend remains in thaw service.
        _ = checksum_verifier

        return True, None

    def exists(self, storage_reference: str, location: ColdStorageLocation) -> bool:
        return Path(storage_reference).exists()

    def delete(self, storage_reference: str, location: ColdStorageLocation) -> Tuple[bool, Optional[str]]:
        ref = Path(storage_reference)
        try:
            if ref.exists():
                ref.unlink()
            return True, None
        except Exception as exc:
            return False, str(exc)
