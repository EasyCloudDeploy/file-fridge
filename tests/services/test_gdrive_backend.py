import httpx
import pytest
import respx

from app.models import ColdStorageBackendType, ColdStorageLocation, OperationType
from app.services.cold_storage_backends.gdrive_backend import GoogleDriveColdStorageBackend


@pytest.mark.unit
class TestGoogleDriveColdStorageBackend:
    @respx.mock
    def test_list_files_page_error_surfaces_google_detail(self, monkeypatch):
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
