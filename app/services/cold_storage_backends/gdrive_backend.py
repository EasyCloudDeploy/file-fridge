"""Google Drive cold storage backend."""

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

import httpx

from app.models import ColdStorageLocation, OperationType
from app.services.checksum_verifier import checksum_verifier
from app.services.cold_storage_backends.base import ColdStorageBackend, ColdStorageCapabilities

logger = logging.getLogger(__name__)

# Files larger than this threshold use the resumable upload API instead of multipart.
# Google enforces a 5 MB hard cap on the complete multipart request body.
_MULTIPART_SIZE_LIMIT = 5 * 1024 * 1024  # 5 MB


class GoogleDriveColdStorageBackend(ColdStorageBackend):
    _TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    _API_BASE = "https://www.googleapis.com/drive/v3"
    _UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files"

    # In-memory access-token cache: {(location_id, refresh_token): (access_token, expires_at)}
    # Keyed on refresh_token so a rotated token invalidates the cache automatically.
    _token_cache: Dict[Tuple[int, str], Tuple[str, float]] = {}
    _token_cache_lock = threading.Lock()

    def backend_name(self) -> str:
        return "gdrive"

    def capabilities(self) -> ColdStorageCapabilities:
        return ColdStorageCapabilities(
            supports_move=True,
            supports_copy=True,
            supports_symlink=False,
            supports_local_path_stats=False,
        )

    def _get_config(self, location: ColdStorageLocation) -> Dict[str, Any]:
        return location.get_backend_config() or {}

    def _get_access_token(self, location: ColdStorageLocation) -> str:
        """Return a valid access token, refreshing only when the cached one has expired."""
        config = self._get_config(location)
        client_id = config.get("client_id")
        client_secret = config.get("client_secret")
        refresh_token = config.get("refresh_token")

        if not client_id or not client_secret:
            raise RuntimeError("Google Drive credentials are incomplete")
        if not refresh_token:
            raise RuntimeError("Google Drive account is not connected (missing refresh token)")

        cache_key = (location.id, refresh_token)

        with self._token_cache_lock:
            cached = self._token_cache.get(cache_key)
            # Leave a 60-second buffer before expiry so we don't use a token that
            # expires mid-request.
            if cached and time.time() < cached[1] - 60:
                return cached[0]

        # Token missing or expired — fetch a fresh one outside the lock to avoid
        # blocking other threads during the network round-trip.
        response = httpx.post(
            self._TOKEN_ENDPOINT,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20.0,
        )
        self._raise_for_status_with_google_detail(response, "Google Drive token refresh failed")
        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise RuntimeError("Google Drive token response missing access_token")

        # Google typically issues tokens valid for 3600 seconds.
        expires_in = int(payload.get("expires_in") or 3600)
        expires_at = time.time() + expires_in

        with self._token_cache_lock:
            self._token_cache[cache_key] = (access_token, expires_at)

        return access_token

    def _auth_headers(self, location: ColdStorageLocation) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._get_access_token(location)}"}

    def _location_scope_prefix(self, location: ColdStorageLocation) -> str:
        if location.path.startswith("gdrive://"):
            return location.path.removeprefix("gdrive://")
        return location.path

    def _folder_id(self, location: ColdStorageLocation) -> Optional[str]:
        folder_id = (self._get_config(location).get("folder_id") or "").strip()
        return folder_id or None

    def _resolve_folder_by_id(self, folder_id: str, headers: Dict[str, str]) -> Optional[str]:
        response = httpx.get(
            f"{self._API_BASE}/files/{quote(folder_id)}",
            params={"fields": "id,mimeType", "supportsAllDrives": "true"},
            headers=headers,
            timeout=20.0,
        )
        if response.status_code == 404:
            return None
        self._raise_for_status_with_google_detail(response, "Google Drive folder lookup failed")
        payload = response.json()
        if payload.get("mimeType") != "application/vnd.google-apps.folder":
            return None
        return payload.get("id")

    def _resolve_folder_by_name(self, folder_name: str, headers: Dict[str, str]) -> Optional[str]:
        escaped_folder_name = folder_name.replace("'", "\\'")
        response = httpx.get(
            f"{self._API_BASE}/files",
            params={
                "q": (
                    "trashed=false and "
                    "mimeType='application/vnd.google-apps.folder' and "
                    f"name='{escaped_folder_name}'"
                ),
                "fields": "files(id,name,createdTime)",
                "pageSize": 1,
                "orderBy": "createdTime desc",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
            headers=headers,
            timeout=20.0,
        )
        self._raise_for_status_with_google_detail(response, "Google Drive folder search failed")
        files = response.json().get("files", [])
        if not files:
            return None
        return files[0].get("id")

    def _create_folder(self, folder_name: str, headers: Dict[str, str]) -> str:
        response = httpx.post(
            f"{self._API_BASE}/files",
            params={"fields": "id", "supportsAllDrives": "true"},
            headers={**headers, "Content-Type": "application/json"},
            json={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            },
            timeout=20.0,
        )
        self._raise_for_status_with_google_detail(response, "Google Drive folder creation failed")
        folder_id = (response.json() or {}).get("id")
        if not folder_id:
            raise RuntimeError("Google Drive folder creation returned no folder id")
        return folder_id

    def _ensure_folder_id(self, location: ColdStorageLocation) -> Optional[str]:
        folder_value = self._folder_id(location)
        if not folder_value:
            return None

        headers = self._auth_headers(location)

        by_id = self._resolve_folder_by_id(folder_value, headers)
        if by_id:
            return by_id

        by_name = self._resolve_folder_by_name(folder_value, headers)
        if by_name:
            return by_name

        return self._create_folder(folder_value, headers)

    def _parse_reference(self, storage_reference: str) -> str:
        parsed = urlparse(storage_reference)
        if parsed.scheme != "gdrive":
            raise RuntimeError(f"Invalid Google Drive storage reference: {storage_reference}")
        clean_path = unquote(parsed.path.lstrip("/"))
        combined = "/".join(part for part in [unquote(parsed.netloc), clean_path] if part)
        if not combined:
            raise RuntimeError(f"Invalid Google Drive storage reference: {storage_reference}")
        file_id = combined.split("/")[-1]
        if not file_id:
            raise RuntimeError(f"Invalid Google Drive storage reference: {storage_reference}")
        return file_id

    def _build_multipart_body(
        self, metadata: Dict[str, Any], source_path: Path, boundary: str
    ) -> Tuple[bytes, str]:
        """Build a multipart/related body. Only call for files ≤ _MULTIPART_SIZE_LIMIT."""
        metadata_part = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        file_bytes = source_path.read_bytes()
        content_type = "application/octet-stream"

        body = (
            b"--"
            + boundary.encode("utf-8")
            + b"\r\n"
            + b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + metadata_part
            + b"\r\n--"
            + boundary.encode("utf-8")
            + b"\r\n"
            + f"Content-Type: {content_type}\r\n\r\n".encode()
            + file_bytes
            + b"\r\n--"
            + boundary.encode("utf-8")
            + b"--\r\n"
        )
        return body, f"multipart/related; boundary={boundary}"

    def _resumable_upload(
        self,
        metadata: Dict[str, Any],
        source_path: Path,
        headers: Dict[str, str],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> str:
        """Upload a file using the resumable upload API. Returns the new file ID."""
        file_size = source_path.stat().st_size

        # Step 1: Initiate the resumable session.
        init_resp = httpx.post(
            self._UPLOAD_ENDPOINT,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": "id",
            },
            headers={
                **headers,
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "application/octet-stream",
                "X-Upload-Content-Length": str(file_size),
            },
            content=json.dumps(metadata, separators=(",", ":")).encode("utf-8"),
            timeout=30.0,
        )
        self._raise_for_status_with_google_detail(
            init_resp, "Google Drive resumable upload initiation failed"
        )
        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            raise RuntimeError("Google Drive resumable upload initiation returned no upload URL")

        # Step 2: Stream the file to the upload URL in chunks.
        chunk_size = 8 * 1024 * 1024  # 8 MB chunks (must be a multiple of 256 KB)
        bytes_sent = 0
        file_id = None

        with source_path.open("rb") as fh:
            while bytes_sent < file_size:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                end_byte = bytes_sent + len(chunk) - 1
                upload_resp = httpx.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {bytes_sent}-{end_byte}/{file_size}",
                        "Content-Type": "application/octet-stream",
                    },
                    content=chunk,
                    timeout=120.0,
                )
                # 308 Resume Incomplete is expected for all chunks except the last.
                if upload_resp.status_code == 308:
                    bytes_sent = end_byte + 1
                    if progress_callback is not None:
                        progress_callback(bytes_sent)
                    continue
                # 200 or 201 signals the upload is complete.
                if upload_resp.status_code in (200, 201):
                    bytes_sent = end_byte + 1
                    if progress_callback is not None:
                        progress_callback(bytes_sent)
                    file_id = upload_resp.json().get("id")
                    break
                self._raise_for_status_with_google_detail(
                    upload_resp, "Google Drive resumable upload chunk failed"
                )
                bytes_sent = end_byte + 1

        if not file_id:
            raise RuntimeError("Google Drive resumable upload completed but returned no file ID")
        return file_id

    @staticmethod
    def _raise_for_status_with_google_detail(response: httpx.Response, context: str) -> None:
        if response.is_success:
            return
        detail = None
        try:
            payload = response.json()
            error_obj = payload.get("error")
            if isinstance(error_obj, dict):
                detail = error_obj.get("message")
            elif isinstance(error_obj, str):
                detail = error_obj
        except Exception:
            detail = None

        if detail:
            raise RuntimeError(f"{context}: {detail}")
        response.raise_for_status()

    def _build_app_query(self, location: ColdStorageLocation) -> str:
        query_parts = [
            "trashed=false",
            "appProperties has { key='file_fridge_managed' and value='true' }",
            f"appProperties has {{ key='ff_location_id' and value='{location.id}' }}",
        ]
        folder_id = self._ensure_folder_id(location)
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        return " and ".join(query_parts)

    def _build_folder_query(self, location: ColdStorageLocation) -> str:
        """Query for all non-trashed files in the configured folder, app-managed or not."""
        query_parts = ["trashed=false", "mimeType!='application/vnd.google-apps.folder'"]
        folder_id = self._ensure_folder_id(location)
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        return " and ".join(query_parts)

    def _list_files_page(
        self,
        location: ColdStorageLocation,
        query: str,
        page_size: int,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "q": query,
            "pageSize": max(1, min(page_size, 1000)),
            "fields": "nextPageToken,files(id,name,size,mimeType,createdTime,modifiedTime,appProperties)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "orderBy": "modifiedTime desc",
        }
        if page_token:
            params["pageToken"] = page_token

        response = httpx.get(
            f"{self._API_BASE}/files",
            params=params,
            headers=self._auth_headers(location),
            timeout=30.0,
        )
        self._raise_for_status_with_google_detail(response, "Google Drive file listing failed")
        return response.json()

    def _list_app_files_page(
        self, location: ColdStorageLocation, page_size: int, page_token: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._list_files_page(
            location, self._build_app_query(location), page_size, page_token
        )

    @staticmethod
    def _normalise_file_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw Drive API file object to the canonical File Fridge dict."""
        app_props = item.get("appProperties") or {}
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "size": int(item.get("size") or 0),
            "mime_type": item.get("mimeType"),
            "created_time": item.get("createdTime"),
            "modified_time": item.get("modifiedTime"),
            "relative_path": app_props.get("ff_relative_path"),
            # Whether this file was uploaded by File Fridge.
            "is_managed": app_props.get("file_fridge_managed") == "true",
            # The location ID recorded in appProperties (may differ from the current location).
            "ff_location_id": app_props.get("ff_location_id"),
        }

    def list_managed_files(
        self, location: ColdStorageLocation, page_size: int = 100, page_token: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = self._list_app_files_page(location, page_size=page_size, page_token=page_token)
        files = [self._normalise_file_item(item) for item in payload.get("files", [])]
        return {
            "files": files,
            "next_page_token": payload.get("nextPageToken"),
        }

    def list_all_folder_files(
        self, location: ColdStorageLocation, page_size: int = 100, page_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """List every file in the configured folder, including externally-added ones.

        Each returned item includes ``is_managed`` (bool) and ``ff_location_id`` (str|None)
        so callers can distinguish files owned by this location, files owned by another
        File Fridge location, and files that were added outside the application.
        """
        payload = self._list_files_page(
            location, self._build_folder_query(location), page_size, page_token
        )
        files = [self._normalise_file_item(item) for item in payload.get("files", [])]
        return {
            "files": files,
            "next_page_token": payload.get("nextPageToken"),
        }

    def get_usage_stats(self, location: ColdStorageLocation) -> Dict[str, Optional[int]]:
        """Return Drive quota statistics.

        app_used_bytes is omitted here because computing it requires paginating every managed
        file — potentially hundreds of API calls for large locations. The dedicated
        /gdrive/stats endpoint should be used when app-level usage is needed.
        """
        response = httpx.get(
            f"{self._API_BASE}/about",
            params={"fields": "storageQuota(limit,usage,usageInDrive,usageInDriveTrash)"},
            headers=self._auth_headers(location),
            timeout=20.0,
        )
        response.raise_for_status()
        quota = response.json().get("storageQuota") or {}

        total_limit = int(quota.get("limit") or 0)
        total_used = int(quota.get("usage") or 0)
        free_bytes = max(0, total_limit - total_used) if total_limit else None

        return {
            "total_limit_bytes": total_limit or None,
            "total_used_bytes": total_used,
            "free_bytes": free_bytes,
            "usage_in_drive_bytes": int(quota.get("usageInDrive") or 0),
            "usage_in_drive_trash_bytes": int(quota.get("usageInDriveTrash") or 0),
            "app_used_bytes": None,
        }

    def get_app_usage_bytes(self, location: ColdStorageLocation) -> int:
        """Compute total bytes of File Fridge managed files by paginating the Drive API.

        This is intentionally separate from get_usage_stats because it is expensive
        (one API call per 1,000 files). Call it only from endpoints that explicitly
        need the per-app usage figure.
        """
        app_used = 0
        page_token = None
        while True:
            listing = self._list_app_files_page(location, page_size=1000, page_token=page_token)
            for file_item in listing.get("files", []):
                app_used += int(file_item.get("size") or 0)
            page_token = listing.get("nextPageToken")
            if not page_token:
                break
        return app_used

    def validate_location(self, location: ColdStorageLocation) -> Tuple[bool, Optional[str]]:
        config = self._get_config(location)
        if not config.get("client_id"):
            return False, "Missing Google Drive client ID"
        if not config.get("client_secret"):
            return False, "Missing Google Drive client secret"
        return True, None

    def build_reference(self, location: ColdStorageLocation, relative_path: Path) -> str:
        scope_prefix = self._location_scope_prefix(location)
        return f"gdrive://{scope_prefix}/{quote(relative_path.as_posix())}"

    def freeze_file(
        self,
        source_path: Path,
        relative_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        if operation_mode == OperationType.SYMLINK:
            return False, "Operation 'symlink' is not supported by backend 'gdrive'", None, None

        try:
            metadata: Dict[str, Any] = {
                "name": relative_path.name,
                "appProperties": {
                    "file_fridge_managed": "true",
                    "ff_location_id": str(location.id),
                    "ff_relative_path": relative_path.as_posix(),
                    "ff_uploaded_at": str(int(time.time())),
                },
            }
            folder_id = self._ensure_folder_id(location)
            if folder_id:
                metadata["parents"] = [folder_id]

            # Calculate checksum BEFORE upload/deletion so it is never None for MOVE.
            checksum_before = checksum_verifier.calculate_checksum(source_path)

            headers = self._auth_headers(location)
            file_size = source_path.stat().st_size

            if file_size <= _MULTIPART_SIZE_LIMIT:
                # Small file: use multipart upload (single request, lower overhead).
                boundary = f"file-fridge-{uuid.uuid4().hex}"
                body, content_type = self._build_multipart_body(metadata, source_path, boundary)
                response = httpx.post(
                    self._UPLOAD_ENDPOINT,
                    params={
                        "uploadType": "multipart",
                        "supportsAllDrives": "true",
                        "fields": "id,size",
                    },
                    headers={**headers, "Content-Type": content_type},
                    content=body,
                    timeout=120.0,
                )
                self._raise_for_status_with_google_detail(response, "Google Drive upload failed")
                file_id = response.json().get("id")
                if not file_id:
                    return False, "Google Drive upload response missing file id", None, None
                if progress_callback is not None:
                    progress_callback(file_size)
            else:
                # Large file: use resumable upload to avoid the 5 MB multipart cap
                # and to avoid loading the entire file into memory.
                file_id = self._resumable_upload(metadata, source_path, headers, progress_callback)

            if operation_mode == OperationType.MOVE:
                source_path.unlink()

            scope_prefix = self._location_scope_prefix(location)
            storage_reference = f"gdrive://{scope_prefix}/{quote(file_id)}"
            return True, None, storage_reference, checksum_before
        except Exception as exc:
            return False, f"Google Drive freeze failed: {exc}", None, None

    def _stream_to_local(
        self, file_id: str, destination_path: Path, headers: Dict[str, str]
    ) -> None:
        """Stream a Drive file to a local path using an atomic temp-then-replace write."""
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
        if temp_path.exists():
            temp_path.unlink()

        with httpx.stream(
            "GET",
            f"{self._API_BASE}/files/{quote(file_id)}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=headers,
            timeout=120.0,
        ) as response:
            self._raise_for_status_with_google_detail(response, "Google Drive download failed")
            with temp_path.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if chunk:
                        handle.write(chunk)

        temp_path.replace(destination_path)

    def download_file(
        self,
        storage_reference: str,
        destination_path: Path,
        location: ColdStorageLocation,
    ) -> Tuple[bool, Optional[str]]:
        """Download a file to a local path without removing it from Drive."""
        try:
            file_id = self._parse_reference(storage_reference)
            headers = self._auth_headers(location)

            metadata_resp = httpx.get(
                f"{self._API_BASE}/files/{quote(file_id)}",
                params={"fields": "id,mimeType", "supportsAllDrives": "true"},
                headers=headers,
                timeout=20.0,
            )
            self._raise_for_status_with_google_detail(
                metadata_resp, "Google Drive metadata fetch failed"
            )
            mime_type = metadata_resp.json().get("mimeType") or ""
            if mime_type.startswith("application/vnd.google-apps."):
                return False, "Google Workspace native files cannot be downloaded"

            self._stream_to_local(file_id, destination_path, headers)
            return True, None
        except Exception as exc:
            if destination_path.exists():
                destination_path.unlink()
            return False, f"Google Drive download failed: {exc}"

    def thaw_file(
        self,
        storage_reference: str,
        destination_path: Path,
        location: ColdStorageLocation,
        operation_mode: OperationType,
    ) -> Tuple[bool, Optional[str]]:
        if operation_mode == OperationType.SYMLINK:
            return False, "Operation 'symlink' is not supported by backend 'gdrive'"

        try:
            file_id = self._parse_reference(storage_reference)
            headers = self._auth_headers(location)

            metadata_resp = httpx.get(
                f"{self._API_BASE}/files/{quote(file_id)}",
                params={"fields": "id,mimeType", "supportsAllDrives": "true"},
                headers=headers,
                timeout=20.0,
            )
            self._raise_for_status_with_google_detail(
                metadata_resp, "Google Drive metadata fetch failed"
            )
            metadata = metadata_resp.json()
            mime_type = metadata.get("mimeType") or ""
            if mime_type.startswith("application/vnd.google-apps."):
                return False, "Google Workspace native files are not supported for thaw"

            if operation_mode == OperationType.COPY and destination_path.exists():
                delete_resp = httpx.delete(
                    f"{self._API_BASE}/files/{quote(file_id)}",
                    params={"supportsAllDrives": "true"},
                    headers=headers,
                    timeout=20.0,
                )
                self._raise_for_status_with_google_detail(delete_resp, "Google Drive delete failed")
                return True, None

            self._stream_to_local(file_id, destination_path, headers)

            if operation_mode in (OperationType.MOVE, OperationType.COPY):
                delete_resp = httpx.delete(
                    f"{self._API_BASE}/files/{quote(file_id)}",
                    params={"supportsAllDrives": "true"},
                    headers=headers,
                    timeout=20.0,
                )
                self._raise_for_status_with_google_detail(delete_resp, "Google Drive delete failed")

            return True, None
        except Exception as exc:
            return False, f"Google Drive thaw failed: {exc}"

    def exists(self, storage_reference: str, location: ColdStorageLocation) -> bool:
        file_id = self._parse_reference(storage_reference)
        response = httpx.get(
            f"{self._API_BASE}/files/{quote(file_id)}",
            params={"fields": "id", "supportsAllDrives": "true"},
            headers=self._auth_headers(location),
            timeout=20.0,
        )
        if response.status_code == 404:
            return False
        self._raise_for_status_with_google_detail(response, "Google Drive existence check failed")
        return True

    def delete(
        self, storage_reference: str, location: ColdStorageLocation
    ) -> Tuple[bool, Optional[str]]:
        try:
            file_id = self._parse_reference(storage_reference)
            response = httpx.delete(
                f"{self._API_BASE}/files/{quote(file_id)}",
                params={"supportsAllDrives": "true"},
                headers=self._auth_headers(location),
                timeout=20.0,
            )
            self._raise_for_status_with_google_detail(response, "Google Drive delete failed")
            return True, None
        except Exception as exc:
            return False, str(exc)
