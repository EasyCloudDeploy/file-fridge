"""Additional storage router coverage."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models import (
    ColdStorageBackendType,
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    OperationType,
    StorageType,
)
from app.routers.api import storage as storage_router


@pytest.mark.unit
class TestStorageRouterExtended:
    def test_can_tolerate_offline_helpers(self):
        loc = ColdStorageLocation(
            name="Removable",
            path="/mnt/drive",
            backend_type=ColdStorageBackendType.LOCAL,
            allow_offline=True,
        )
        loc.local_drive_is_removable = True
        loc.local_drive_is_connected = False
        assert storage_router._can_tolerate_offline(loc, None) is True

        loc.local_drive_is_connected = True
        assert storage_router._can_tolerate_offline(loc, "Path not found") is True
        assert storage_router._can_tolerate_offline(loc, "permission denied") is False

    def test_parse_gdrive_state_invalid(self):
        with pytest.raises(Exception):
            storage_router._parse_gdrive_state("not-valid-base64!!!")

    def test_parse_gdrive_state_expired(self, monkeypatch):
        monkeypatch.setattr(storage_router.settings, "secret_key", "test-secret-key")
        payload = {"location_id": 5, "ts": int(time.time()) - 3600}
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        import base64
        import hashlib
        import hmac

        sig = hmac.new(b"test-secret-key", payload_bytes, hashlib.sha256).hexdigest()
        token = base64.urlsafe_b64encode(
            json.dumps(
                {"payload": payload, "sig": sig}, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        ).decode("utf-8")
        with pytest.raises(Exception):
            storage_router._parse_gdrive_state(token)

    def test_storage_stats_missing_local_path(self, authenticated_client, db_session, tmp_path):
        missing = ColdStorageLocation(name="Missing Path", path=str(tmp_path / "does-not-exist"))
        db_session.add(missing)
        db_session.commit()

        response = authenticated_client.get("/api/v1/storage/stats")
        assert response.status_code == 200
        errors = [row for row in response.json() if row.get("error")]
        assert any(missing.path in row["path"] for row in errors)

    def test_storage_stats_remote_gdrive(self, authenticated_client, db_session, monkeypatch):
        location = ColdStorageLocation(
            name="Stats Remote",
            path="gdrive://stats",
            backend_type=ColdStorageBackendType.GDRIVE,
            operation_mode=OperationType.MOVE,
        )
        location.set_backend_config({"refresh_token": "token"})
        db_session.add(location)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.get_usage_stats.return_value = {
            "total_limit_bytes": 1000,
            "total_used_bytes": 400,
            "free_bytes": 600,
        }
        monkeypatch.setattr(storage_router, "get_backend", lambda _loc: mock_backend)

        response = authenticated_client.get("/api/v1/storage/stats")
        assert response.status_code == 200
        row = next(item for item in response.json() if item["path"] == "gdrive://stats")
        assert row["total_bytes"] == 1000
        assert row["used_bytes"] == 400

    def test_pause_unpause_and_recall(
        self,
        authenticated_client,
        db_session,
        storage_location,
        monitored_path_factory,
        monkeypatch,
    ):
        from app.services.scheduler import scheduler_service

        monkeypatch.setattr(scheduler_service, "trigger_recall_job", lambda _id: None)

        pause = authenticated_client.post(f"/api/v1/storage/locations/{storage_location.id}/pause")
        assert pause.status_code == 200
        assert pause.json()["paused"] is True

        unpause = authenticated_client.post(
            f"/api/v1/storage/locations/{storage_location.id}/unpause"
        )
        assert unpause.status_code == 200
        assert unpause.json()["paused"] is False

        monitored = monitored_path_factory("Recall Path", "/tmp/hot-recall")
        from datetime import datetime, timezone

        inv = FileInventory(
            path_id=monitored.id,
            file_path="/cold/file.bin",
            file_size=10,
            file_mtime=datetime.now(timezone.utc),
            storage_type=StorageType.COLD,
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(inv)
        db_session.commit()

        recall = authenticated_client.post(
            f"/api/v1/storage/locations/{storage_location.id}/recall"
        )
        assert recall.status_code == 202
        assert recall.json()["files_queued"] >= 1

    def test_sanitize_backend_config_redacts_secrets(self):
        sanitized = storage_router._sanitize_backend_config(
            {
                "client_id": "abc",
                "client_secret": "secret",
                "refresh_token": "token",
                "folder_id": "folder",
            }
        )
        assert sanitized["client_secret"] == "***REDACTED***"
        assert sanitized["refresh_token"] == "***REDACTED***"
        assert sanitized["folder_id"] == "folder"

    def test_create_s3_storage_location(self, authenticated_client, db_session):
        payload = {
            "name": "S3 Bucket API",
            "path": "placeholder",
            "backend_type": ColdStorageBackendType.S3.value,
            "backend_config": {"bucket": "my-bucket", "prefix": "archive"},
        }
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 201
        assert response.json()["path"] == "s3://my-bucket/archive"

    def test_create_storage_duplicate_path(self, authenticated_client, storage_location, tmp_path):
        dup_path = tmp_path / "dup_storage"
        dup_path.mkdir()
        payload = {"name": "Unique Name Dup Path", "path": storage_location.path}
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 400
        assert "path" in response.json()["detail"].lower()

    def test_parse_gdrive_state_invalid_signature(self, monkeypatch):
        monkeypatch.setattr(storage_router.settings, "secret_key", "test-secret-key")
        import base64
        import json

        payload = {"location_id": 3, "ts": int(time.time())}
        token = base64.urlsafe_b64encode(
            json.dumps({"payload": payload, "sig": "bad-signature"}, separators=(",", ":")).encode(
                "utf-8"
            )
        ).decode("utf-8")
        with pytest.raises(Exception):
            storage_router._parse_gdrive_state(token)

    def test_list_and_get_storage_locations(
        self, authenticated_client, storage_location, db_session
    ):
        response = authenticated_client.get("/api/v1/storage/locations")
        assert response.status_code == 200
        assert any(row["id"] == storage_location.id for row in response.json())

        detail = authenticated_client.get(f"/api/v1/storage/locations/{storage_location.id}")
        assert detail.status_code == 200
        assert detail.json()["name"] == storage_location.name

        missing = authenticated_client.get("/api/v1/storage/locations/999999")
        assert missing.status_code == 404

    def test_update_storage_location(self, authenticated_client, storage_location):
        payload = {
            "name": "Updated Storage Name",
            "path": storage_location.path,
            "caution_threshold_percent": 25,
            "critical_threshold_percent": 5,
        }
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}", json=payload
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Storage Name"

    def test_create_storage_duplicate_name(self, authenticated_client, storage_location, tmp_path):
        payload = {"name": storage_location.name, "path": str(tmp_path / "other_cold")}
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 400

    def test_create_gdrive_location_uses_name_when_folder_missing(
        self, authenticated_client, db_session
    ):
        payload = {
            "name": "Drive Folder By Name",
            "path": "placeholder",
            "backend_type": ColdStorageBackendType.GDRIVE.value,
            "backend_config": {"client_id": "id", "client_secret": "secret"},
        }
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 201
        assert response.json()["path"] == "gdrive://Drive Folder By Name"

    def test_gdrive_files_and_stats_endpoints(self, authenticated_client, db_session, monkeypatch):
        location = ColdStorageLocation(
            name="GDrive API",
            path="gdrive://api-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        location.set_backend_config({"client_id": "x", "client_secret": "y"})
        db_session.add(location)
        db_session.commit()

        mock_backend = MagicMock()
        mock_backend.list_managed_files.return_value = {
            "files": [{"id": "1", "name": "a.txt"}],
            "next_page_token": None,
        }
        mock_backend.get_usage_stats.return_value = {
            "total_limit_bytes": 100,
            "total_used_bytes": 10,
            "free_bytes": 90,
        }
        mock_backend.get_app_usage_bytes.return_value = 5
        monkeypatch.setattr(storage_router, "get_backend", lambda _loc: mock_backend)

        files_resp = authenticated_client.get(
            f"/api/v1/storage/locations/{location.id}/gdrive/files"
        )
        assert files_resp.status_code == 200
        assert files_resp.json()["files"][0]["name"] == "a.txt"

        stats_resp = authenticated_client.get(
            f"/api/v1/storage/locations/{location.id}/gdrive/stats"
        )
        assert stats_resp.status_code == 200
        assert stats_resp.json()["app_used_bytes"] == 5

    def test_gdrive_endpoints_wrong_backend(self, authenticated_client, storage_location):
        assert (
            authenticated_client.get(
                f"/api/v1/storage/locations/{storage_location.id}/gdrive/files"
            ).status_code
            == 400
        )

    def test_gdrive_oauth_start_and_metadata(self, authenticated_client, db_session, monkeypatch):
        location = ColdStorageLocation(
            name="OAuth Drive",
            path="gdrive://oauth",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        location.set_backend_config(
            {"client_id": "client", "client_secret": "secret", "folder_id": "folder"}
        )
        db_session.add(location)
        db_session.commit()
        location_id = location.id

        meta = authenticated_client.get("/api/v1/storage/gdrive/oauth/metadata")
        assert meta.status_code == 200
        assert "callback_url" in meta.json()

        start = authenticated_client.post(
            f"/api/v1/storage/locations/{location_id}/gdrive/oauth/start"
        )
        assert start.status_code == 200
        assert "accounts.google.com" in start.json()["auth_url"]

    def test_recall_and_pause_not_found(self, authenticated_client):
        assert (
            authenticated_client.post("/api/v1/storage/locations/999999/pause").status_code == 404
        )
        assert (
            authenticated_client.post("/api/v1/storage/locations/999999/recall").status_code == 404
        )

    def test_delete_storage_location_in_use(
        self, authenticated_client, storage_location, monitored_path_factory, tmp_path
    ):
        monitored_path_factory("Uses Storage", str(tmp_path / "uses_storage_hot"))
        response = authenticated_client.delete(f"/api/v1/storage/locations/{storage_location.id}")
        assert response.status_code == 400

    def test_force_delete_storage_clears_inventory_and_records(
        self,
        authenticated_client,
        db_session,
        file_inventory_factory,
        tmp_path,
    ):
        cold_dir = tmp_path / "force_delete_cold"
        cold_dir.mkdir()
        loc = ColdStorageLocation(name="Force Delete Loc", path=str(cold_dir))
        db_session.add(loc)
        db_session.commit()

        cold_file = cold_dir / "purge_me.bin"
        cold_file.write_bytes(b"purge")
        inv = file_inventory_factory(
            str(cold_file),
            storage_type=StorageType.COLD,
            cold_storage_location=loc,
        )
        inv.cold_storage_location_id = loc.id
        db_session.add(
            FileRecord(
                path_id=inv.path_id,
                original_path=str(Path(inv.file_path).parent.parent / "hot" / "purge_me.bin"),
                cold_storage_path=str(cold_file),
                cold_storage_location_id=loc.id,
                file_size=5,
                operation_type=OperationType.MOVE,
            )
        )
        db_session.commit()
        loc_id = loc.id

        response = authenticated_client.delete(f"/api/v1/storage/locations/{loc_id}?force=true")
        assert response.status_code == 200
        assert db_session.get(ColdStorageLocation, loc_id) is None
        assert db_session.get(FileInventory, inv.id) is None
        assert (
            db_session.query(FileRecord)
            .filter(FileRecord.cold_storage_location_id == loc_id)
            .count()
            == 0
        )

    def test_storage_stats_s3_non_gdrive_remote(
        self, authenticated_client, db_session, monkeypatch
    ):
        location = ColdStorageLocation(
            name="S3 Stats",
            path="s3://stats-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        db_session.add(location)
        db_session.commit()
        response = authenticated_client.get("/api/v1/storage/stats")
        assert response.status_code == 200
        row = next(item for item in response.json() if item["path"] == "s3://stats-bucket")
        assert row.get("error")

    def test_update_storage_location_duplicate_name(
        self, authenticated_client, storage_location, db_session, tmp_path
    ):
        other = ColdStorageLocation(name="Other Storage", path=str(tmp_path / "other_storage"))
        (tmp_path / "other_storage").mkdir()
        db_session.add(other)
        db_session.commit()
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"name": other.name},
        )
        assert response.status_code == 400

    def test_update_storage_location_s3_path_and_backend_config(
        self, authenticated_client, db_session, monkeypatch
    ):
        from app.services.scheduler import scheduler_service

        monkeypatch.setattr(scheduler_service, "trigger_encryption_job", lambda _id: None)
        loc = ColdStorageLocation(
            name="S3 Update",
            path="s3://old-bucket/old",
            backend_type=ColdStorageBackendType.S3,
        )
        loc.set_backend_config({"bucket": "old-bucket", "prefix": "old", "client_secret": "sec"})
        db_session.add(loc)
        db_session.commit()

        response = authenticated_client.put(
            f"/api/v1/storage/locations/{loc.id}",
            json={
                "backend_config": {
                    "bucket": "new-bucket",
                    "prefix": "archive",
                    "client_secret": "***REDACTED***",
                },
                "is_encrypted": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["path"] == "s3://new-bucket/archive"
        assert body["is_encrypted"] is True

    def test_update_storage_location_gdrive_path_from_name(self, authenticated_client, db_session):
        loc = ColdStorageLocation(
            name="GDrive Rename Folder",
            path="gdrive://old-folder",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        loc.set_backend_config({"client_id": "id", "client_secret": "secret", "folder_id": ""})
        db_session.add(loc)
        db_session.commit()

        response = authenticated_client.put(
            f"/api/v1/storage/locations/{loc.id}",
            json={
                "backend_config": {
                    "refresh_token": "",
                    "access_token": None,
                    "folder_id": "",
                }
            },
        )
        assert response.status_code == 200
        assert response.json()["path"] == "gdrive://GDrive Rename Folder"

    def test_update_storage_location_creates_local_path(
        self, authenticated_client, storage_location, tmp_path
    ):
        new_path = tmp_path / "created_on_update"
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"path": str(new_path)},
        )
        assert response.status_code == 200
        assert new_path.is_dir()

    def test_update_storage_location_path_not_directory(
        self, authenticated_client, storage_location, tmp_path
    ):
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("x")
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"path": str(file_path)},
        )
        assert response.status_code == 400

    def test_update_storage_location_duplicate_path(
        self, authenticated_client, storage_location, db_session, tmp_path
    ):
        dup = ColdStorageLocation(name="Dup Path Loc", path=str(tmp_path / "dup_path_loc"))
        (tmp_path / "dup_path_loc").mkdir()
        db_session.add(dup)
        db_session.commit()
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"path": dup.path},
        )
        assert response.status_code == 400

    def test_update_storage_location_validation_failure(
        self, authenticated_client, storage_location, monkeypatch
    ):
        mock_backend = MagicMock()
        mock_backend.validate_location.return_value = (False, "backend unreachable")
        monkeypatch.setattr(storage_router, "get_backend", lambda _loc: mock_backend)
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"name": "Validation Fail Name"},
        )
        assert response.status_code == 400

    def test_update_storage_encryption_job_schedule_failure(
        self, authenticated_client, storage_location, monkeypatch
    ):
        from app.services.scheduler import scheduler_service

        def _boom(_id):
            raise RuntimeError("scheduler down")

        monkeypatch.setattr(scheduler_service, "trigger_encryption_job", _boom)
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"is_encrypted": True},
        )
        assert response.status_code == 500
        assert "encryption job" in response.json()["detail"].lower()

    def test_update_storage_decryption_job_schedule_failure(
        self, authenticated_client, db_session, storage_location, monkeypatch
    ):
        from app.services.scheduler import scheduler_service

        loc = db_session.get(ColdStorageLocation, storage_location.id)
        loc.is_encrypted = True
        loc.encryption_status = "encrypted"
        db_session.commit()

        def _boom(_id):
            raise RuntimeError("scheduler down")

        monkeypatch.setattr(scheduler_service, "trigger_decryption_job", _boom)
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}",
            json={"is_encrypted": False},
        )
        assert response.status_code == 500
        assert "decryption job" in response.json()["detail"].lower()

    def test_google_oauth_callback_success(self, client, db_session, monkeypatch):
        location = ColdStorageLocation(
            name="OAuth Success",
            path="gdrive://oauth-success",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        location.set_backend_config(
            {"client_id": "client", "client_secret": "secret", "folder_id": "oauth-success"}
        )
        db_session.add(location)
        db_session.commit()
        location_id = location.id
        state = storage_router._make_gdrive_state(location_id)

        token_response = MagicMock()
        token_response.raise_for_status = MagicMock()
        token_response.json.return_value = {
            "refresh_token": "refresh-abc",
            "access_token": "access-xyz",
            "expires_in": 3600,
        }
        monkeypatch.setattr(storage_router.httpx, "post", MagicMock(return_value=token_response))

        response = client.get(
            f"/api/v1/storage/gdrive/oauth/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert response.status_code in (302, 307)
        assert (
            f"/storage-locations/{location_id}/edit?gdrive_oauth=success"
            in response.headers["location"]
        )

    def test_google_oauth_callback_error_and_invalid_location(self, client, db_session):
        err = client.get(
            "/api/v1/storage/gdrive/oauth/callback?error=access_denied",
            follow_redirects=False,
        )
        assert "reason=access_denied" in err.headers["location"]

        state = storage_router._make_gdrive_state(999999)
        invalid = client.get(
            f"/api/v1/storage/gdrive/oauth/callback?code=x&state={state}",
            follow_redirects=False,
        )
        assert "invalid_location" in invalid.headers["location"]

    def test_google_oauth_callback_missing_refresh_token(self, client, db_session, monkeypatch):
        location = ColdStorageLocation(
            name="OAuth No Refresh",
            path="gdrive://oauth-no-refresh",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        location.set_backend_config({"client_id": "client", "client_secret": "secret"})
        db_session.add(location)
        db_session.commit()
        location_id = location.id
        state = storage_router._make_gdrive_state(location_id)

        token_response = MagicMock()
        token_response.raise_for_status = MagicMock()
        token_response.json.return_value = {"access_token": "access-only"}
        monkeypatch.setattr(storage_router.httpx, "post", MagicMock(return_value=token_response))

        response = client.get(
            f"/api/v1/storage/gdrive/oauth/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert "missing_refresh_token" in response.headers["location"]

    def test_google_oauth_callback_missing_client_credentials(self, client, db_session):
        location = ColdStorageLocation(
            name="OAuth Missing Creds",
            path="gdrive://oauth-missing-creds",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        location.set_backend_config({"client_id": "", "client_secret": ""})
        db_session.add(location)
        db_session.commit()
        location_id = location.id
        state = storage_router._make_gdrive_state(location_id)
        response = client.get(
            f"/api/v1/storage/gdrive/oauth/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert "missing_client_credentials" in response.headers["location"]

    def test_google_oauth_callback_generic_failure(self, client, db_session, monkeypatch):
        location = ColdStorageLocation(
            name="OAuth Generic Fail",
            path="gdrive://oauth-generic-fail",
            backend_type=ColdStorageBackendType.GDRIVE,
        )
        location.set_backend_config({"client_id": "client", "client_secret": "secret"})
        db_session.add(location)
        db_session.commit()
        location_id = location.id
        state = storage_router._make_gdrive_state(location_id)

        token_response = MagicMock()
        token_response.raise_for_status.side_effect = RuntimeError("token exchange failed")
        monkeypatch.setattr(storage_router.httpx, "post", MagicMock(return_value=token_response))

        response = client.get(
            f"/api/v1/storage/gdrive/oauth/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert "callback_failed" in response.headers["location"]
