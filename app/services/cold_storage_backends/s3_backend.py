"""Amazon S3 cold storage backend."""

from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

from app.models import ColdStorageLocation, OperationType
from app.services.checksum_verifier import checksum_verifier
from app.services.cold_storage_backends.base import ColdStorageBackend, ColdStorageCapabilities


class S3ColdStorageBackend(ColdStorageBackend):
    def backend_name(self) -> str:
        return "s3"

    def capabilities(self) -> ColdStorageCapabilities:
        return ColdStorageCapabilities(
            supports_move=True,
            supports_copy=True,
            supports_symlink=False,
            supports_local_path_stats=False,
        )

    def _client(self, location: ColdStorageLocation):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required to use S3 cold storage backends") from exc

        cfg = location.get_backend_config()
        session = boto3.session.Session(
            aws_access_key_id=cfg.get("access_key_id") or None,
            aws_secret_access_key=cfg.get("secret_access_key") or None,
            aws_session_token=cfg.get("session_token") or None,
            region_name=cfg.get("region") or None,
        )
        return session.client("s3", endpoint_url=cfg.get("endpoint_url") or None)

    def _bucket_prefix(self, location: ColdStorageLocation) -> Tuple[str, str]:
        cfg = location.get_backend_config()
        bucket = cfg.get("bucket")
        if not bucket:
            raise RuntimeError("Missing S3 backend config: 'bucket'")
        prefix = (cfg.get("prefix") or "").strip("/")
        return bucket, prefix

    def _key_for_relative(self, location: ColdStorageLocation, relative_path: Path) -> str:
        bucket, prefix = self._bucket_prefix(location)
        rel = str(relative_path).replace("\\", "/").lstrip("/")
        _ = bucket
        if prefix:
            return f"{prefix}/{rel}" if rel else prefix
        return rel

    def _parse_reference(self, storage_reference: str) -> Tuple[str, str]:
        parsed = urlparse(storage_reference)
        if parsed.scheme != "s3":
            raise RuntimeError(f"Invalid S3 storage reference: {storage_reference}")
        bucket = parsed.netloc
        key = unquote(parsed.path.lstrip("/"))
        if not bucket or not key:
            raise RuntimeError(f"Invalid S3 storage reference: {storage_reference}")
        return bucket, key

    def validate_location(self, location: ColdStorageLocation) -> Tuple[bool, Optional[str]]:
        try:
            bucket, _ = self._bucket_prefix(location)
            if not bucket.strip():
                return False, "Missing S3 backend config: 'bucket'"
            return True, None
        except Exception as exc:
            return False, f"S3 backend validation failed: {exc}"

    def build_reference(self, location: ColdStorageLocation, relative_path: Path) -> str:
        bucket, _prefix = self._bucket_prefix(location)
        key = self._key_for_relative(location, relative_path)
        return f"s3://{bucket}/{quote(key)}"

    def freeze_file(
        self,
        source_path: Path,
        relative_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        if operation_mode == OperationType.SYMLINK:
            return False, "Operation 'symlink' is not supported by backend 's3'", None, None

        try:
            bucket, _prefix = self._bucket_prefix(location)
            key = self._key_for_relative(location, relative_path)
            client = self._client(location)

            # Calculate checksum BEFORE upload/deletion so it is never None for MOVE.
            checksum_before = checksum_verifier.calculate_checksum(source_path)

            client.upload_file(str(source_path), bucket, key)

            if operation_mode == OperationType.MOVE:
                source_path.unlink()

            return True, None, f"s3://{bucket}/{quote(key)}", checksum_before
        except Exception as exc:
            return False, f"S3 freeze failed: {exc}", None, None

    def thaw_file(
        self,
        storage_reference: str,
        destination_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
    ) -> Tuple[bool, Optional[str]]:
        if operation_mode == OperationType.SYMLINK:
            return False, "Operation 'symlink' is not supported by backend 's3'"

        try:
            bucket, key = self._parse_reference(storage_reference)
            client = self._client(location)

            if operation_mode == OperationType.COPY and destination_path.exists():
                client.delete_object(Bucket=bucket, Key=key)
                return True, None

            destination_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
            if temp_path.exists():
                temp_path.unlink()

            client.download_file(bucket, key, str(temp_path))
            temp_path.replace(destination_path)

            if operation_mode in (OperationType.MOVE, OperationType.COPY):
                client.delete_object(Bucket=bucket, Key=key)

            return True, None
        except Exception as exc:
            return False, f"S3 thaw failed: {exc}"

    def exists(self, storage_reference: str, location: ColdStorageLocation) -> bool:
        try:
            bucket, key = self._parse_reference(storage_reference)
            client = self._client(location)
            client.head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(
        self, storage_reference: str, location: ColdStorageLocation
    ) -> Tuple[bool, Optional[str]]:
        try:
            bucket, key = self._parse_reference(storage_reference)
            client = self._client(location)
            client.delete_object(Bucket=bucket, Key=key)
            return True, None
        except Exception as exc:
            return False, str(exc)
