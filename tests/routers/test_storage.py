from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import ColdStorageBackendType, ColdStorageLocation, OperationType


@pytest.mark.unit
class TestStorageRouter:
    def test_list_storage_locations(self, authenticated_client, storage_location):
        """Test listing all storage locations."""
        response = authenticated_client.get("/api/v1/storage/locations")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        # Fixture might already have created one
        names = [loc["name"] for loc in data]
        assert storage_location.name in names

    def test_create_storage_location_success(self, authenticated_client, tmp_path):
        """Test successful creation of a storage location."""
        new_path = tmp_path / "new_storage_api"
        payload = {
            "name": "New API Storage",
            "path": str(new_path)
        }
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 201
        assert response.json()["name"] == "New API Storage"
        assert new_path.exists()
        assert new_path.is_dir()

    def test_create_storage_location_duplicate_name(self, authenticated_client, storage_location):
        """Test creating a storage location with a duplicate name."""
        payload = {
            "name": storage_location.name,
            "path": "/tmp/different_path",
        }
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"].lower()

    def test_get_storage_stats(self, authenticated_client, storage_location):
        """Test getting storage statistics."""
        # Ensure path exists
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)

        response = authenticated_client.get("/api/v1/storage/stats")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        paths = [s["path"] for s in data]
        assert storage_location.path in paths
        assert "total_bytes" in data[0]

    def test_get_storage_location_not_found(self, authenticated_client):
        """Test getting a non-existent storage location."""
        response = authenticated_client.get("/api/v1/storage/locations/9999")
        assert response.status_code == 404

    def test_update_storage_location_success(self, authenticated_client, storage_location):
        """Test updating a storage location."""
        payload = {"name": "Newly Updated Name"}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json=payload
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Newly Updated Name"

    def test_update_storage_location_allow_offline_toggle(self, authenticated_client, storage_location):
        """Test enabling allow_offline on a local storage location."""
        payload = {"allow_offline": True}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json=payload,
        )
        assert response.status_code == 200
        assert response.json()["allow_offline"] is True

    def test_delete_storage_location_success(self, authenticated_client, db_session, tmp_path):
        """Test deleting an unused storage location."""
        loc_path = tmp_path / "to_delete_api"
        loc_path.mkdir()
        loc = ColdStorageLocation(name="Delete API", path=str(loc_path))
        db_session.add(loc)
        db_session.commit()

        response = authenticated_client.delete(f"/api/v1/storage/locations/{loc.id}")
        assert response.status_code == 200
        assert db_session.get(ColdStorageLocation, loc.id) is None

    def test_delete_storage_location_in_use(self, authenticated_client, db_session, monitored_path_factory, storage_location):
        """Test deleting a storage location that is in use (should fail without force)."""
        # monitored_path_factory uses the storage_location fixture
        monitored_path_factory("In Use Path", "/tmp/hot_in_use")

        response = authenticated_client.delete(f"/api/v1/storage/locations/{storage_location.id}")
        assert response.status_code == 400
        assert "still associated" in response.json()["detail"].lower()

    def test_force_delete_storage_location(self, authenticated_client, db_session, tmp_path, storage_location):
        """Test force deleting a storage location."""
        # Setup files in location
        loc_path = Path(storage_location.path)
        loc_path.mkdir(parents=True, exist_ok=True)
        test_file = loc_path / "to_be_purged.txt"
        test_file.write_text("goodbye world")

        response = authenticated_client.delete(f"/api/v1/storage/locations/{storage_location.id}?force=true")
        assert response.status_code == 200
        assert not test_file.exists()
        assert not loc_path.exists()
        assert db_session.get(ColdStorageLocation, storage_location.id) is None

    def test_toggle_encryption_on(self, authenticated_client, storage_location, monkeypatch):
        """Test enabling encryption for a storage location."""
        # Mock scheduler to avoid actual background job triggering errors
        from app.services.scheduler import scheduler_service
        monkeypatch.setattr(scheduler_service, "trigger_encryption_job", lambda x: None)

        payload = {"is_encrypted": True}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_encrypted"] is True
        assert data["encryption_status"] == "pending"

    def test_toggle_encryption_off(self, authenticated_client, db_session, storage_location, monkeypatch):
        """Test disabling encryption for a storage location."""
        # Mock scheduler
        from app.services.scheduler import scheduler_service
        monkeypatch.setattr(scheduler_service, "trigger_decryption_job", lambda x: None)

        # Manually set to encrypted state first
        loc = db_session.get(ColdStorageLocation, storage_location.id)
        loc.is_encrypted = True
        loc.encryption_status = "encrypted"
        db_session.commit()

        payload = {"is_encrypted": False}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_encrypted"] is False
        assert data["encryption_status"] == "decrypting"

    def test_google_oauth_callback_is_public(self, client):
        """Google OAuth callback should not require API authentication."""
        response = client.get("/api/v1/storage/gdrive/oauth/callback", follow_redirects=False)
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/storage-locations?gdrive_oauth=error&reason=missing_code"

    def test_google_oauth_callback_returns_network_unreachable_on_connect_error(
        self, client, db_session, monkeypatch
    ):
        """Google OAuth callback should surface connectivity failures clearly."""
        import httpx

        from app.routers.api import storage as storage_router

        location = ColdStorageLocation(
            name="Google OAuth Callback Network Error Location",
            path="gdrive://oauth-callback-test",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "folder_id": "oauth-callback-test",
            }
        )
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)

        state = storage_router._make_gdrive_state(location.id)

        def _raise_connect_error(*_args, **_kwargs):
            raise httpx.ConnectError("Network is unreachable")

        monkeypatch.setattr(storage_router.httpx, "post", _raise_connect_error)

        response = client.get(
            f"/api/v1/storage/gdrive/oauth/callback?code=test-code&state={state}",
            follow_redirects=False,
        )
        assert response.status_code in (302, 307)
        assert (
            response.headers["location"]
            == "/storage-locations?gdrive_oauth=error&reason=network_unreachable"
        )

    def test_google_oauth_metadata_prefers_configured_instance_url(
        self, authenticated_client, monkeypatch
    ):
        """OAuth callback URL should use configured public instance URL when available."""
        from app.routers.api import storage as storage_router

        monkeypatch.setattr(
            storage_router.instance_config_service,
            "get_instance_url",
            lambda _db: "https://filefridge.example.com",
        )
        response = authenticated_client.get("/api/v1/storage/gdrive/oauth/metadata")
        assert response.status_code == 200
        assert (
            response.json()["callback_url"]
            == "https://filefridge.example.com/api/v1/storage/gdrive/oauth/callback"
        )

    def test_google_oauth_start_uses_configured_instance_url_for_redirect_uri(
        self, authenticated_client, db_session, monkeypatch
    ):
        """OAuth start URL should use public configured callback URL."""
        from app.routers.api import storage as storage_router

        location = ColdStorageLocation(
            name="Google OAuth Start URL Location",
            path="gdrive://oauth-start-test",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "folder_id": "oauth-start-test",
            }
        )
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)

        monkeypatch.setattr(
            storage_router.instance_config_service,
            "get_instance_url",
            lambda _db: "https://filefridge.example.com",
        )
        response = authenticated_client.post(
            f"/api/v1/storage/locations/{location.id}/gdrive/oauth/start"
        )
        assert response.status_code == 200

        auth_url = response.json()["auth_url"]
        parsed = urlparse(auth_url)
        params = parse_qs(parsed.query)
        assert params["redirect_uri"] == [
            "https://filefridge.example.com/api/v1/storage/gdrive/oauth/callback"
        ]

    def test_google_drive_files_listing_endpoint(
        self, authenticated_client, db_session, monkeypatch
    ):
        """Test listing managed Google Drive files for a storage location."""
        location = ColdStorageLocation(
            name="Google Drive Test Location",
            path="gdrive://file-fridge-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "file-fridge-folder",
            }
        )
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)

        from app.services.cold_storage_backends.gdrive_backend import GoogleDriveColdStorageBackend

        monkeypatch.setattr(
            GoogleDriveColdStorageBackend,
            "list_managed_files",
            lambda _self, _location, page_size=100, page_token=None: {
                "files": [
                    {
                        "id": "file-1",
                        "name": "archive.zip",
                        "size": 1024,
                        "mime_type": "application/zip",
                        "relative_path": "backups/archive.zip",
                    }
                ],
                "next_page_token": None,
            },
        )

        response = authenticated_client.get(f"/api/v1/storage/locations/{location.id}/gdrive/files")
        assert response.status_code == 200
        payload = response.json()
        assert payload["location_id"] == location.id
        assert len(payload["files"]) == 1
        assert payload["files"][0]["id"] == "file-1"
        assert payload["files"][0]["size"] == 1024

    def test_google_drive_stats_endpoint(self, authenticated_client, db_session, monkeypatch):
        """Test Google Drive usage stats endpoint."""
        location = ColdStorageLocation(
            name="Google Drive Stats Location",
            path="gdrive://file-fridge-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "file-fridge-folder",
            }
        )
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)

        from app.services.cold_storage_backends.gdrive_backend import GoogleDriveColdStorageBackend

        monkeypatch.setattr(
            GoogleDriveColdStorageBackend,
            "get_usage_stats",
            lambda _self, _location: {
                "total_limit_bytes": 10000,
                "total_used_bytes": 2500,
                "free_bytes": 7500,
                "usage_in_drive_bytes": 2400,
                "usage_in_drive_trash_bytes": 100,
                "app_used_bytes": None,
            },
        )
        monkeypatch.setattr(
            GoogleDriveColdStorageBackend,
            "get_app_usage_bytes",
            lambda _self, _location: 1024,
        )

        response = authenticated_client.get(f"/api/v1/storage/locations/{location.id}/gdrive/stats")
        assert response.status_code == 200
        payload = response.json()
        assert payload["location_id"] == location.id
        assert payload["drive_total_bytes"] == 10000
        assert payload["drive_used_bytes"] == 2500
        assert payload["drive_free_bytes"] == 7500
        assert payload["app_used_bytes"] == 1024

    def test_google_drive_update_does_not_clear_refresh_token_on_null(
        self, authenticated_client, db_session
    ):
        """Null/empty token fields in generic updates must not drop OAuth credentials."""
        location = ColdStorageLocation(
            name="Google Drive Token Preserve Location",
            path="gdrive://file-fridge-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "folder_id": "file-fridge-folder",
            }
        )
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)

        response = authenticated_client.put(
            f"/api/v1/storage/locations/{location.id}",
            json={
                "backend_config": {
                    "folder_id": "updated-folder-id",
                    "refresh_token": None,
                    "access_token": "",
                    "access_token_expires_at": None,
                }
            },
        )
        assert response.status_code == 200

        location_from_db = (
            db_session.query(ColdStorageLocation)
            .filter(ColdStorageLocation.id == location.id)
            .first()
        )
        assert location_from_db is not None
        cfg = location_from_db.get_backend_config()
        assert cfg["folder_id"] == "updated-folder-id"
        assert cfg["refresh_token"] == "refresh-token"
