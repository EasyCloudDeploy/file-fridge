import httpx
import pytest
import respx

from app.models import ColdStorageBackendType, ColdStorageLocation, OperationType
from app.services.cold_storage_backends.gdrive_backend import GoogleDriveColdStorageBackend


@pytest.mark.unit
class TestGoogleDriveColdStorageBackend:
    @respx.mock
    def test_list_files_page_error_surfaces_google_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that a failure during file listing correctly raises RuntimeError with Google details."""
        backend = GoogleDriveColdStorageBackend()

        # Create a mock ColdStorageLocation
        location = ColdStorageLocation(
            id=999,
            name="Google Drive Test",
            path="gdrive://test-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "test-folder-id",
            }
        )

        # Mock the folder ID resolution and access token to avoid network calls
        monkeypatch.setattr(backend, "_ensure_folder_id", lambda _loc: "test-folder-id")
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "dummy-access-token")

        # Mock the Google Drive files listing endpoint to return an error response
        error_json = {
            "error": {
                "code": 403,
                "message": "The user does not have sufficient permissions for this file.",
                "errors": [
                    {
                        "domain": "global",
                        "reason": "insufficientPermissions",
                        "message": "The user does not have sufficient permissions for this file.",
                    }
                ],
            }
        }
        respx.get("https://www.googleapis.com/drive/v3/files").mock(
            return_value=httpx.Response(403, json=error_json)
        )

        # Call the method and assert it raises a RuntimeError with the Google API details
        with pytest.raises(RuntimeError) as exc_info:
            backend._list_files_page(
                location=location,
                query="trashed=false",
                page_size=10,
            )

        assert "Google Drive file listing failed" in str(exc_info.value)
        assert "The user does not have sufficient permissions for this file." in str(exc_info.value)

    @respx.mock
    def test_exists_returns_false_on_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that exists() returns False when Google Drive API returns 404."""
        backend = GoogleDriveColdStorageBackend()

        location = ColdStorageLocation(
            id=999,
            name="Google Drive Test",
            path="gdrive://test-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "test-folder-id",
            }
        )

        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "dummy-access-token")

        file_id = "test-file-id"
        respx.get(f"https://www.googleapis.com/drive/v3/files/{file_id}").mock(
            return_value=httpx.Response(404)
        )

        ref = "gdrive://test-folder/test-file-id"
        assert backend.exists(ref, location) is False

    @respx.mock
    def test_exists_raises_on_non_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that exists() propagates the error when a non-404 error is returned."""
        backend = GoogleDriveColdStorageBackend()

        location = ColdStorageLocation(
            id=999,
            name="Google Drive Test",
            path="gdrive://test-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "test-folder-id",
            }
        )

        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "dummy-access-token")

        file_id = "test-file-id"
        error_json = {
            "error": {
                "code": 500,
                "message": "Internal Server Error",
            }
        }
        respx.get(f"https://www.googleapis.com/drive/v3/files/{file_id}").mock(
            return_value=httpx.Response(500, json=error_json)
        )

        ref = "gdrive://test-folder/test-file-id"
        with pytest.raises(RuntimeError) as exc_info:
            backend.exists(ref, location)

        assert "Google Drive existence check failed" in str(exc_info.value)
        assert "Internal Server Error" in str(exc_info.value)

    @pytest.fixture
    def gdrive_location(self) -> ColdStorageLocation:
        location = ColdStorageLocation(
            id=42,
            name="Google Drive Test",
            path="gdrive://test-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "folder-id-123",
            }
        )
        return location

    @respx.mock
    def test_get_access_token_fetches_and_caches(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        backend._token_cache.clear()

        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "token-abc", "expires_in": 3600})
        )

        token1 = backend._get_access_token(gdrive_location)
        token2 = backend._get_access_token(gdrive_location)
        assert token1 == "token-abc"
        assert token2 == "token-abc"
        assert respx.calls.call_count == 1

    def test_get_access_token_missing_credentials(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        gdrive_location.set_backend_config({"client_id": "", "client_secret": ""})
        with pytest.raises(RuntimeError, match="credentials are incomplete"):
            backend._get_access_token(gdrive_location)

    @respx.mock
    def test_validate_location_success(self, gdrive_location: ColdStorageLocation) -> None:
        backend = GoogleDriveColdStorageBackend()
        ok, err = backend.validate_location(gdrive_location)
        assert ok is True
        assert err is None

    def test_validate_location_missing_client_id(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        gdrive_location.set_backend_config({"client_secret": "secret"})
        ok, err = backend.validate_location(gdrive_location)
        assert ok is False
        assert "client ID" in (err or "")

    def test_parse_reference_and_build_reference(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        file_id = backend._parse_reference("gdrive://scope/path/file-id-xyz")
        assert file_id == "file-id-xyz"

        ref = backend.build_reference(gdrive_location, __import__("pathlib").Path("a/b.txt"))
        assert ref.startswith("gdrive://test-folder/")
        assert "a/b.txt" in ref

    def test_normalise_file_item(self) -> None:
        backend = GoogleDriveColdStorageBackend()
        item = {
            "id": "fid",
            "name": "demo.txt",
            "size": "123",
            "mimeType": "text/plain",
            "createdTime": "2020-01-01T00:00:00Z",
            "modifiedTime": "2020-01-02T00:00:00Z",
            "appProperties": {
                "file_fridge_managed": "true",
                "ff_relative_path": "a/demo.txt",
                "ff_location_id": "42",
            },
        }
        normalised = backend._normalise_file_item(item)
        assert normalised["is_managed"] is True
        assert normalised["relative_path"] == "a/demo.txt"
        assert normalised["size"] == 123

    @respx.mock
    def test_list_managed_files(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_ensure_folder_id", lambda _loc: "folder-id-123")
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.get("https://www.googleapis.com/drive/v3/files").mock(
            return_value=httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "f1",
                            "name": "one.txt",
                            "size": "10",
                            "appProperties": {"file_fridge_managed": "true"},
                        }
                    ],
                    "nextPageToken": "next-1",
                },
            )
        )

        payload = backend.list_managed_files(gdrive_location, page_size=10)
        assert len(payload["files"]) == 1
        assert payload["next_page_token"] == "next-1"

    @respx.mock
    def test_get_usage_stats(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.get("https://www.googleapis.com/drive/v3/about").mock(
            return_value=httpx.Response(
                200,
                json={
                    "storageQuota": {
                        "limit": "1000",
                        "usage": "400",
                        "usageInDrive": "350",
                        "usageInDriveTrash": "50",
                    }
                },
            )
        )

        stats = backend.get_usage_stats(gdrive_location)
        assert stats["total_limit_bytes"] == 1000
        assert stats["free_bytes"] == 600
        assert stats["app_used_bytes"] is None

    @respx.mock
    def test_delete_success(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.delete("https://www.googleapis.com/drive/v3/files/file-del").mock(
            return_value=httpx.Response(204)
        )

        ok, err = backend.delete("gdrive://test-folder/file-del", gdrive_location)
        assert ok is True
        assert err is None

    @respx.mock
    def test_freeze_file_multipart_small_file(
        self,
        gdrive_location: ColdStorageLocation,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        source = tmp_path / "small.bin"
        source.write_bytes(b"tiny")

        monkeypatch.setattr(backend, "_ensure_folder_id", lambda _loc: "folder-id-123")
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.post("https://www.googleapis.com/upload/drive/v3/files").mock(
            return_value=httpx.Response(200, json={"id": "uploaded-id", "size": "4"})
        )

        ok, err, ref, checksum = backend.freeze_file(
            source, __import__("pathlib").Path("small.bin"), gdrive_location, OperationType.MOVE
        )
        assert ok is True
        assert err is None
        assert "uploaded-id" in (ref or "")
        assert checksum is not None
        assert not source.exists()

    def test_freeze_file_symlink_unsupported(
        self, gdrive_location: ColdStorageLocation, tmp_path
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        source = tmp_path / "x.bin"
        source.write_bytes(b"x")
        ok, err, ref, checksum = backend.freeze_file(
            source, __import__("pathlib").Path("x.bin"), gdrive_location, OperationType.SYMLINK
        )
        assert ok is False
        assert "symlink" in (err or "").lower()

    @respx.mock
    def test_download_file_rejects_google_workspace_native(
        self, gdrive_location: ColdStorageLocation, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.get("https://www.googleapis.com/drive/v3/files/doc-id").mock(
            return_value=httpx.Response(
                200, json={"id": "doc-id", "mimeType": "application/vnd.google-apps.document"}
            )
        )

        dest = tmp_path / "out.bin"
        ok, err = backend.download_file("gdrive://test-folder/doc-id", dest, gdrive_location)
        assert ok is False
        assert "Google Workspace" in (err or "")

    @respx.mock
    def test_thaw_copy_when_destination_exists(
        self, gdrive_location: ColdStorageLocation, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.get("https://www.googleapis.com/drive/v3/files/file-copy").mock(
            return_value=httpx.Response(200, json={"id": "file-copy", "mimeType": "text/plain"})
        )
        respx.delete("https://www.googleapis.com/drive/v3/files/file-copy").mock(
            return_value=httpx.Response(204)
        )

        dest = tmp_path / "existing.bin"
        dest.write_bytes(b"exists")

        ok, err = backend.thaw_file(
            "gdrive://test-folder/file-copy", dest, gdrive_location, OperationType.COPY
        )
        assert ok is True
        assert err is None

    def test_backend_metadata_helpers(self, gdrive_location: ColdStorageLocation) -> None:
        backend = GoogleDriveColdStorageBackend()
        assert backend.backend_name() == "gdrive"
        caps = backend.capabilities()
        assert caps.supports_move is True
        assert caps.supports_symlink is False

        gdrive_location.path = "/plain/path"
        assert backend._location_scope_prefix(gdrive_location) == "/plain/path"
        assert backend._folder_id(gdrive_location) == "folder-id-123"

        with pytest.raises(RuntimeError, match="Invalid Google Drive"):
            backend._parse_reference("s3://bucket/key")
        with pytest.raises(RuntimeError, match="Invalid Google Drive"):
            backend._parse_reference("gdrive://")

    @respx.mock
    def test_ensure_folder_id_resolves_by_id(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")
        respx.get("https://www.googleapis.com/drive/v3/files/folder-id-123").mock(
            return_value=httpx.Response(
                200, json={"id": "folder-id-123", "mimeType": "application/vnd.google-apps.folder"}
            )
        )
        assert backend._ensure_folder_id(gdrive_location) == "folder-id-123"

    @respx.mock
    def test_ensure_folder_id_resolves_by_name_then_creates(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        gdrive_location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "My Folder",
            }
        )
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.get("https://www.googleapis.com/drive/v3/files/My%20Folder").mock(
            return_value=httpx.Response(404)
        )
        respx.get("https://www.googleapis.com/drive/v3/files").mock(
            return_value=httpx.Response(200, json={"files": []})
        )
        respx.post("https://www.googleapis.com/drive/v3/files").mock(
            return_value=httpx.Response(200, json={"id": "created-folder"})
        )
        assert backend._ensure_folder_id(gdrive_location) == "created-folder"

    @respx.mock
    def test_resolve_folder_by_id_rejects_non_folder(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")
        respx.get("https://www.googleapis.com/drive/v3/files/folder-id-123").mock(
            return_value=httpx.Response(200, json={"id": "folder-id-123", "mimeType": "text/plain"})
        )
        assert backend._resolve_folder_by_id("folder-id-123", {"Authorization": "Bearer t"}) is None

    @respx.mock
    def test_get_access_token_missing_refresh_token(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        gdrive_location.set_backend_config(
            {"client_id": "id", "client_secret": "secret", "refresh_token": ""}
        )
        with pytest.raises(RuntimeError, match="missing refresh token"):
            backend._get_access_token(gdrive_location)

    @respx.mock
    def test_get_access_token_missing_access_token_in_response(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        backend._token_cache.clear()
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(200, json={"expires_in": 3600})
        )
        with pytest.raises(RuntimeError, match="missing access_token"):
            backend._get_access_token(gdrive_location)

    def test_validate_location_missing_client_secret(
        self, gdrive_location: ColdStorageLocation
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        gdrive_location.set_backend_config({"client_id": "id"})
        ok, err = backend.validate_location(gdrive_location)
        assert ok is False
        assert "client secret" in (err or "").lower()

    @respx.mock
    def test_list_all_folder_files(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_ensure_folder_id", lambda _loc: "folder-id-123")
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")
        respx.get("https://www.googleapis.com/drive/v3/files").mock(
            return_value=httpx.Response(
                200,
                json={
                    "files": [{"id": "ext", "name": "external.txt", "size": "5"}],
                    "nextPageToken": None,
                },
            )
        )
        payload = backend.list_all_folder_files(gdrive_location)
        assert len(payload["files"]) == 1
        assert payload["files"][0]["is_managed"] is False

    @respx.mock
    def test_get_app_usage_bytes_paginates(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_ensure_folder_id", lambda _loc: "folder-id-123")
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        route = respx.get("https://www.googleapis.com/drive/v3/files")
        route.side_effect = [
            httpx.Response(
                200,
                json={"files": [{"size": "100"}], "nextPageToken": "page-2"},
            ),
            httpx.Response(200, json={"files": [{"size": "50"}], "nextPageToken": None}),
        ]
        assert backend.get_app_usage_bytes(gdrive_location) == 150

    @respx.mock
    def test_resumable_upload_and_large_freeze(
        self,
        gdrive_location: ColdStorageLocation,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        source = tmp_path / "large.bin"
        source.write_bytes(b"x" * (6 * 1024 * 1024))

        monkeypatch.setattr(backend, "_ensure_folder_id", lambda _loc: "folder-id-123")
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        respx.post("https://www.googleapis.com/upload/drive/v3/files").mock(
            return_value=httpx.Response(
                200,
                headers={"Location": "https://upload.example/resume"},
                json={"id": "ignored"},
            )
        )
        respx.put("https://upload.example/resume").mock(
            return_value=httpx.Response(200, json={"id": "resumable-id"})
        )

        progress_calls = []

        ok, err, ref, checksum = backend.freeze_file(
            source,
            __import__("pathlib").Path("large.bin"),
            gdrive_location,
            OperationType.MOVE,
            progress_callback=progress_calls.append,
        )
        assert ok is True
        assert err is None
        assert "resumable-id" in (ref or "")
        assert checksum is not None
        assert progress_calls

    @respx.mock
    def test_download_file_success(
        self, gdrive_location: ColdStorageLocation, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        def _drive_get(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"payload")
            return httpx.Response(200, json={"id": "dl-id", "mimeType": "text/plain"})

        respx.get(url__regex=r"https://www\.googleapis\.com/drive/v3/files/dl-id").mock(
            side_effect=_drive_get
        )

        dest = tmp_path / "downloaded.txt"
        ok, err = backend.download_file("gdrive://test-folder/dl-id", dest, gdrive_location)
        assert ok is True
        assert err is None
        assert dest.read_bytes() == b"payload"

    @respx.mock
    def test_thaw_move_downloads_and_deletes(
        self, gdrive_location: ColdStorageLocation, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")

        def _drive_get(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"thawed")
            return httpx.Response(200, json={"id": "thaw-id", "mimeType": "text/plain"})

        respx.get(url__regex=r"https://www\.googleapis\.com/drive/v3/files/thaw-id").mock(
            side_effect=_drive_get
        )
        respx.delete("https://www.googleapis.com/drive/v3/files/thaw-id").mock(
            return_value=httpx.Response(204)
        )

        dest = tmp_path / "thawed.txt"
        ok, err = backend.thaw_file(
            "gdrive://test-folder/thaw-id", dest, gdrive_location, OperationType.MOVE
        )
        assert ok is True
        assert err is None
        assert dest.read_bytes() == b"thawed"

    @respx.mock
    def test_exists_returns_true_on_success(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")
        respx.get("https://www.googleapis.com/drive/v3/files/exists-id").mock(
            return_value=httpx.Response(200, json={"id": "exists-id"})
        )
        assert backend.exists("gdrive://test-folder/exists-id", gdrive_location) is True

    @respx.mock
    def test_delete_failure_returns_error(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")
        respx.delete("https://www.googleapis.com/drive/v3/files/bad").mock(
            return_value=httpx.Response(500, json={"error": {"message": "boom"}})
        )
        ok, err = backend.delete("gdrive://test-folder/bad", gdrive_location)
        assert ok is False
        assert "boom" in (err or "")

    def test_raise_for_status_non_json(self) -> None:
        request = httpx.Request("GET", "https://www.googleapis.com/drive/v3/files")
        response = httpx.Response(500, content=b"not json", request=request)
        with pytest.raises(httpx.HTTPStatusError):
            GoogleDriveColdStorageBackend._raise_for_status_with_google_detail(
                response, "Google Drive token refresh failed"
            )

    @respx.mock
    def test_resolve_folder_by_name_returns_newest(
        self, gdrive_location: ColdStorageLocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        monkeypatch.setattr(backend, "_get_access_token", lambda _loc: "token")
        respx.get("https://www.googleapis.com/drive/v3/files").mock(
            return_value=httpx.Response(
                200, json={"files": [{"id": "by-name-id", "name": "Folder"}]}
            )
        )
        folder_id = backend._resolve_folder_by_name("Folder", {"Authorization": "Bearer t"})
        assert folder_id == "by-name-id"

    @respx.mock
    def test_resumable_upload_intermediate_chunk(
        self, gdrive_location: ColdStorageLocation, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = GoogleDriveColdStorageBackend()
        source = tmp_path / "chunked.bin"
        source.write_bytes(b"x" * (9 * 1024 * 1024))
        headers = {"Authorization": "Bearer token"}
        metadata = {"name": "chunked.bin"}

        respx.post("https://www.googleapis.com/upload/drive/v3/files").mock(
            return_value=httpx.Response(200, headers={"Location": "https://upload.example/chunked"})
        )
        respx.put("https://upload.example/chunked").mock(
            side_effect=[
                httpx.Response(308),
                httpx.Response(200, json={"id": "chunked-complete"}),
            ]
        )
        progress = []
        file_id = backend._resumable_upload(metadata, source, headers, progress.append)
        assert file_id == "chunked-complete"
        assert progress

    def test_thaw_symlink_unsupported(self, gdrive_location: ColdStorageLocation, tmp_path) -> None:
        backend = GoogleDriveColdStorageBackend()
        ok, err = backend.thaw_file(
            "gdrive://test-folder/id",
            tmp_path / "out.bin",
            gdrive_location,
            OperationType.SYMLINK,
        )
        assert ok is False
        assert "symlink" in (err or "").lower()
