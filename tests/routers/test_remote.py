import itertools
import json
import time
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import httpx
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    FileInventory,
    FileStatus,
    MonitoredPath,
    RemoteConnection,
    RemoteTransferJob,
    TransferDirection,
    TransferMode,
    TransferStatus,
    TrustStatus,
    ColdStorageLocation,
    StorageType,
)
from app.schemas import RemoteConnectionCreate, RemoteTransferJob as RemoteTransferJobSchema
from app.utils.remote_signature import verify_remote_signature

# Assume authenticated_client, monitored_path_factory, storage_location fixtures are available from conftest.

# Mock the verify_remote_signature dependency for all tests in this file
@pytest.fixture(autouse=True)
def mock_verify_remote_signature():
    conn = MagicMock(spec=RemoteConnection, name="mock_remote_connection")
    conn.id = 1
    conn.name = "Mock Remote"
    conn.url = "http://remote.com"
    conn.remote_fingerprint = "mockfingerprint"
    conn.trust_status = TrustStatus.TRUSTED
    conn.remote_ed25519_public_key = "3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q="
    conn.remote_x25519_public_key = "rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA="
    conn.remote_transfer_mode = TransferMode.BIDIRECTIONAL
    conn.transfer_mode = TransferMode.BIDIRECTIONAL
    conn.effective_bidirectional = True

    async def mock_dep():
        return conn

    app.dependency_overrides[verify_remote_signature] = mock_dep
    with patch("app.routers.api.remote.verify_signature_from_components", new_callable=AsyncMock) as mock_vsfc:
        mock_vsfc.return_value = conn
        yield conn
    app.dependency_overrides.pop(verify_remote_signature, None)

# Mock get_signed_headers for all tests in this file
@pytest.fixture(autouse=True)
def mock_get_signed_headers():
    with patch("app.routers.api.remote.get_signed_headers", new_callable=AsyncMock) as mock:
        mock.return_value = {"Authorization": "Bearer signed-token"}
        yield mock


# ==================================
# Connection Management Endpoints
# ==================================

@patch("app.routers.api.remote.instance_config_service.get_instance_url", return_value="http://localhost")
def test_get_remote_status_configured(mock_get_url, authenticated_client: TestClient):
    """Test get_remote_status when configured."""
    response = authenticated_client.get("/api/v1/remote/status")
    assert response.status_code == 200
    assert response.json()["configured"] is True


@patch("app.routers.api.remote.remote_connection_service.get_remote_identity", new_callable=AsyncMock)
@patch("app.routers.api.remote.remote_connection_service.initiate_connection", new_callable=AsyncMock)
@patch("app.routers.api.remote.remote_auth.get_code_with_expiry")
@patch("app.routers.api.remote.instance_config_service.get_instance_url", return_value="http://localhost")
def test_connect_with_code_success(
    mock_get_url,
    mock_get_code,
    mock_initiate_connection,
    mock_get_remote_identity,
    authenticated_client: TestClient,
    remote_connection_factory,
    db_session: Session,
):
    """Test successful connection with a code."""
    mock_get_code.return_value = ("testcode", 3600)
    mock_get_remote_identity.return_value = {
        "instance_name": "Remote", "fingerprint": "remote_fingerprint",
        "ed25519_public_key": "3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=",
        "x25519_public_key": "rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=",
        "url": "http://remote.com",
        "version": "1.0.0"
    }
    real_conn = remote_connection_factory(trust_status=TrustStatus.PENDING)
    mock_initiate_connection.return_value = real_conn

    connection_data = {
        "name": "Remote",
        "url": "http://remote.com",
        "connection_code": "testcode",
        "transfer_mode": "BIDIRECTIONAL",
    }
    response = authenticated_client.post("/api/v1/remote/connect", json=connection_data)

    assert response.status_code == 200


@patch("app.routers.api.remote.remote_connection_service.delete_connection", new_callable=AsyncMock)
def test_delete_connection_success(mock_delete_connection, authenticated_client: TestClient, remote_connection_factory):
    """Test successful deletion of a connection."""
    conn = remote_connection_factory()
    mock_delete_connection.return_value = True

    response = authenticated_client.delete(f"/api/v1/remote/connections/{conn.id}")
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_connection_service.trust_connection")
def test_trust_connection_success(mock_trust_connection, authenticated_client: TestClient, remote_connection_factory):
    """Test successful trusting of a connection."""
    conn = remote_connection_factory(trust_status=TrustStatus.PENDING)
    mock_trust_connection.return_value = conn

    response = authenticated_client.post(f"/api/v1/remote/connections/{conn.id}/trust")
    assert response.status_code == 200


# ==================================
# Transfer Initiation Endpoints
# ==================================

@patch("app.routers.api.remote.remote_transfer_service.create_transfer_job")
def test_migrate_file_success(mock_create_job, authenticated_client: TestClient, remote_connection_factory, remote_transfer_job_factory, monitored_path_factory, tmp_path):
    """Test successful initiation of a file migration."""
    conn = remote_connection_factory()
    path = monitored_path_factory("Local Path", str(tmp_path / "local"))
    job = remote_transfer_job_factory(remote_connection=conn, remote_monitored_path=path)
    mock_create_job.return_value = job

    migration_data = {
        "file_inventory_id": job.file_inventory_id,
        "remote_connection_id": conn.id,
        "remote_monitored_path_id": path.id,
    }
    response = authenticated_client.post("/api/v1/remote/migrate", json=migration_data)

    assert response.status_code == 200


@patch("app.routers.api.remote.remote_transfer_service.create_transfer_job")
def test_bulk_migrate_files_success(mock_create_job, authenticated_client: TestClient, remote_connection_factory, monitored_path_factory, tmp_path):
    """Test bulk initiation of file migrations."""
    conn = remote_connection_factory()
    path = monitored_path_factory("Local Path", str(tmp_path / "local"))
    mock_create_job.return_value = MagicMock(id=1)

    payload = {
        "file_ids": [1, 2, 3],
        "remote_connection_id": conn.id,
        "remote_monitored_path_id": path.id,
    }
    response = authenticated_client.post("/api/v1/remote/migrate/bulk", json=payload)

    assert response.status_code == 200


def test_list_transfers(authenticated_client: TestClient, remote_transfer_job_factory):
    """Test listing all transfer jobs."""
    remote_transfer_job_factory(status="PENDING")
    remote_transfer_job_factory(status="COMPLETED")

    response = authenticated_client.get("/api/v1/remote/transfers")
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_transfer_service.cancel_transfer")
def test_cancel_transfer_success(mock_cancel, authenticated_client: TestClient, remote_transfer_job_factory):
    """Test successful cancellation of a transfer."""
    job = remote_transfer_job_factory(status="PENDING")
    mock_cancel.return_value = True

    response = authenticated_client.post(f"/api/v1/remote/transfers/{job.id}/cancel")
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_transfer_service.cancel_transfer")
def test_bulk_cancel_transfers(mock_cancel, authenticated_client: TestClient, remote_transfer_job_factory):
    """Test bulk cancellation of transfers."""
    job1 = remote_transfer_job_factory(status="PENDING")
    job2 = remote_transfer_job_factory(status="PENDING")
    mock_cancel.return_value = True

    response = authenticated_client.post(f"/api/v1/remote/transfers/bulk/cancel", json=[job1.id, job2.id])
    assert response.status_code == 200


# ==================================
# Instance Config Endpoints
# ==================================

def test_get_instance_config(authenticated_client: TestClient):
    """Test getting instance configuration."""
    response = authenticated_client.get("/api/v1/remote/config")
    assert response.status_code == 200


def test_update_instance_config(authenticated_client: TestClient):
    """Test updating instance configuration."""
    payload = {"instance_name": "New Name"}
    response = authenticated_client.post("/api/v1/remote/config", json=payload)
    assert response.status_code == 200


# ==================================
# Public/Internal Endpoints
# ==================================

@patch("app.routers.api.remote.instance_config_service.get_instance_url", return_value="http://test.com")
@patch("app.routers.api.remote.instance_config_service.get_instance_name", return_value="Test")
@patch("app.routers.api.remote.identity_service.get_instance_fingerprint", return_value="a" * 64)
@patch("app.routers.api.remote.identity_service.get_signing_public_key_str", return_value="3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=")
@patch("app.routers.api.remote.identity_service.get_kx_public_key_str", return_value="rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=")
def test_get_public_identity(mock_kx, mock_ed, mock_fp, mock_name, mock_url, client: TestClient):
    """Test getting public identity (no auth required)."""
    response = client.get("/api/v1/remote/identity")
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_auth.get_code_with_expiry")
@patch("app.routers.api.remote.instance_config_service.get_instance_url", return_value="http://test.com")
def test_get_connection_code(mock_get_url, mock_get_code, authenticated_client: TestClient):
    """Test getting connection code."""
    mock_get_code.return_value = ("test-code", 3600)
    response = authenticated_client.get("/api/v1/remote/connection-code")
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_connection_service.get_remote_identity", new_callable=AsyncMock)
def test_fetch_remote_identity(mock_get_identity, authenticated_client: TestClient):
    """Test fetching identity of a remote instance."""
    mock_get_identity.return_value = {
        "instance_name": "Remote",
        "fingerprint": "a" * 64,
        "ed25519_public_key": "3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=",
        "x25519_public_key": "rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=",
        "url": "http://remote.com",
    }
    response = authenticated_client.post(
        "/api/v1/remote/connections/fetch-identity",
        json={"name": "Remote", "url": "http://remote.com", "connection_code": "dummy"},
    )
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_connection_service.handle_connection_request")
def test_handle_connection_request(mock_handle, client: TestClient):
    """Test handling an incoming connection request."""
    mock_handle.return_value = {
        "identity": {
            "instance_name": "Remote",
            "fingerprint": "a" * 64,
            "ed25519_public_key": "3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=",
            "x25519_public_key": "rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=",
            "url": "http://remote.com/",
        },
        "signature": "deadbeef",
    }
    payload = {
        "identity": {
            "instance_name": "Remote",
            "url": "http://remote.com/",
            "fingerprint": "a" * 64,
            "ed25519_public_key": "3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=",
            "x25519_public_key": "rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=",
        },
        "signature": "deadbeef",
        "connection_code": "code123",
    }
    response = client.post("/api/v1/remote/connection-request", json=payload)
    assert response.status_code == 200


def test_update_connection_success(authenticated_client: TestClient, remote_connection_factory):
    """Test updating a connection."""
    conn = remote_connection_factory()
    payload = {"name": "Updated Name"}
    response = authenticated_client.patch(f"/api/v1/remote/connections/{conn.id}", json=payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"


def test_reject_connection_success(authenticated_client: TestClient, remote_connection_factory):
    """Test rejecting a connection request."""
    conn = remote_connection_factory(trust_status=TrustStatus.PENDING)
    response = authenticated_client.post(f"/api/v1/remote/connections/{conn.id}/reject")
    assert response.status_code == 200


def test_terminate_connection(authenticated_client: TestClient):
    """Test terminating a connection from remote side."""
    response = authenticated_client.post("/api/v1/remote/terminate-connection")
    assert response.status_code == 200


@patch("httpx.AsyncClient.get", new_callable=AsyncMock)
def test_get_remote_paths(mock_get, authenticated_client: TestClient, remote_connection_factory):
    """Test getting exposed paths from a remote instance."""
    conn = remote_connection_factory()
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": 1, "name": "Remote Path"}]
    mock_response.raise_for_status = MagicMock(return_value=None)
    mock_get.return_value = mock_response

    response = authenticated_client.get(f"/api/v1/remote/connections/{conn.id}/paths")
    assert response.status_code == 200


def test_delete_transfer_job(authenticated_client: TestClient, remote_transfer_job_factory, db_session):
    """Test deleting a transfer job."""
    job = remote_transfer_job_factory(status="COMPLETED")
    response = authenticated_client.delete(f"/api/v1/remote/transfers/{job.id}")
    assert response.status_code == 200


def test_bulk_delete_transfers(authenticated_client: TestClient, remote_transfer_job_factory, db_session):
    """Test bulk deleting transfer jobs."""
    job1 = remote_transfer_job_factory(status="COMPLETED")
    job2 = remote_transfer_job_factory(status="FAILED")
    response = authenticated_client.post("/api/v1/remote/transfers/bulk/delete", json=[job1.id, job2.id])
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_transfer_service.run_transfer", new_callable=AsyncMock)
@patch("app.services.scheduler.scheduler_service.trigger_scan")
@patch("app.utils.disk_validator.disk_space_validator.validate_disk_space_direct")
@patch("app.routers.api.remote._decrypt_chunk", new_callable=AsyncMock, return_value=b"data")
@patch("app.routers.api.remote._decompress_chunk", new_callable=AsyncMock, return_value=b"data")
@patch("app.routers.api.remote.anyio.to_thread.run_sync")
@patch("aiofiles.open")
def test_receive_chunk(
    mock_aiofiles_open, mock_run_sync, mock_decompress, mock_decrypt, mock_disk, mock_trigger, mock_run,
    client: TestClient, monitored_path_factory, db_session, mock_verify_remote_signature, tmp_path
):
    """Test receiving a file chunk."""
    mock_run_sync.side_effect = lambda func, *args, **kwargs: func(*args)
    path = monitored_path_factory("Receive", str(tmp_path / "receive"))
    path.storage_locations.append(ColdStorageLocation(name="ColdR", path=str(tmp_path / "coldr")))
    db_session.add(path)
    db_session.commit()

    headers = {
        "X-Chunk-Index": "0",
        "X-Relative-Path": "test.txt",
        "X-Remote-Path-ID": str(path.id),
        "X-Storage-Type": "hot",
        "X-Job-ID": "job1",
        "X-Is-Final": "true",
        "X-Fingerprint": "mockfingerprint",
        "X-Timestamp": str(int(time.time())),
        "X-Nonce": "nonce",
        "X-Signature": "sig",
        "X-File-Size": "100",
    }

    mock_file = AsyncMock()
    mock_aiofiles_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
    mock_aiofiles_open.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.post("/api/v1/remote/receive", headers=headers, content=b"data")
    assert response.status_code == 200


@patch("app.routers.api.remote.scheduler_service.trigger_scan")
@patch("app.services.file_metadata.file_metadata_extractor.compute_sha256", return_value="hash")
@patch("pathlib.Path.rename")
@patch("app.routers.api.remote._get_found_tmp", new_callable=AsyncMock)
def test_verify_transfer_success(
    mock_get_tmp, mock_rename, mock_hash, mock_trigger, authenticated_client: TestClient, tmp_path
):
    """Test transfer verification."""
    tmp_file = tmp_path / "test.fftmp"
    tmp_file.touch()
    mock_get_tmp.return_value = tmp_file

    data = {"relative_path": "test.txt", "remote_path_id": 1, "checksum": "hash"}
    response = authenticated_client.post("/api/v1/remote/verify-transfer", json=data)
    assert response.status_code == 200


@patch("app.routers.api.remote._get_base_directory", return_value="/tmp")
@patch("pathlib.Path.exists", side_effect=[True, False])
@patch("pathlib.Path.stat")
def test_get_transfer_status_completed(mock_stat, mock_exists, mock_get_base, authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test getting transfer status."""
    mock_stat.return_value = MagicMock(st_size=100)
    path = monitored_path_factory("Status", str(tmp_path / "status"))
    response = authenticated_client.get(f"/api/v1/remote/transfer-status?relative_path=test.txt&remote_path_id={path.id}&storage_type=hot")
    assert response.status_code == 200

def test_get_base_directory_no_cold_storage(authenticated_client: TestClient, monitored_path_factory, db_session, tmp_path):
    """Test error handling when a path has no cold storage locations."""
    path = monitored_path_factory("No Cold", str(tmp_path / "no_cold"))
    # The factory adds one default location, we need to remove it
    path.storage_locations = []
    db_session.commit()
    
    response = authenticated_client.get(
        f"/api/v1/remote/transfer-status?relative_path=f.txt&remote_path_id={path.id}&storage_type=cold"
    )
    assert response.status_code == 400
    assert "no cold storage locations" in response.json()["detail"].lower()


def test_bulk_retry_transfers(authenticated_client: TestClient, remote_transfer_job_factory):
    """Test bulk retrying transfers."""
    job = remote_transfer_job_factory(status="FAILED")
    job_id = job.id  # save before session expires
    response = authenticated_client.post("/api/v1/remote/transfers/bulk/retry", json={"job_ids": [job_id]})
    assert response.status_code == 200
    data = response.json()
    assert job_id in data["succeeded"]
    assert not data["failed"]


@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
def test_pull_file_success(mock_post, authenticated_client: TestClient, remote_connection_factory, monitored_path_factory, tmp_path):
    """Test pulling a file."""
    conn = remote_connection_factory()
    local_path = monitored_path_factory("Local Pull", str(tmp_path / "pull_dest"))
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "accepted", "job_id": "r1"}
    mock_response.raise_for_status = MagicMock(return_value=None)
    mock_post.return_value = mock_response

    data = {
        "remote_file_inventory_id": 1,
        "remote_connection_id": conn.id,
        "local_monitored_path_id": local_path.id,
        "strategy": "COPY",
    }
    response = authenticated_client.post("/api/v1/remote/pull", json=data)
    assert response.status_code == 200


def test_exposed_paths(authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test exposed paths."""
    monitored_path_factory("Exposed", str(tmp_path / "exposed"))
    response = authenticated_client.get("/api/v1/remote/exposed-paths")
    assert response.status_code == 200


def test_browse_remote_files(authenticated_client: TestClient, monitored_path_factory, db_session, tmp_path):
    """Test browsing remote files."""
    path = monitored_path_factory("Browse", str(tmp_path / "browse"))
    file_inv = FileInventory(
        path_id=path.id,
        file_path=str(tmp_path / "browse" / "f.txt"),
        file_size=10,
        status=FileStatus.ACTIVE,
        storage_type=StorageType.HOT,
        file_mtime=datetime.now(timezone.utc),
        file_atime=datetime.now(timezone.utc),
        file_ctime=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(file_inv)
    db_session.commit()

    response = authenticated_client.get(f"/api/v1/remote/browse-files?path_id={path.id}")
    assert response.status_code == 200


@patch("app.routers.api.remote.remote_transfer_service.create_transfer_job")
@patch("app.routers.api.remote.remote_transfer_service.run_transfer", new_callable=AsyncMock)
def test_serve_transfer_request(mock_run, mock_create, authenticated_client: TestClient, file_inventory_factory, tmp_path):
    """Test serving a transfer request."""
    file_inv = file_inventory_factory(str(tmp_path / "serve.txt"))
    mock_create.return_value = MagicMock(id=1)

    data = {"file_inventory_id": file_inv.id, "remote_monitored_path_id": 1, "strategy": "COPY"}
    response = authenticated_client.post("/api/v1/remote/serve-transfer", json=data)
    assert response.status_code == 200


def test_sync_transfer_mode(authenticated_client: TestClient):
    """Test syncing transfer mode."""
    response = authenticated_client.post("/api/v1/remote/sync-transfer-mode", json={"transfer_mode": "PUSH_ONLY"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"

def test_connect_with_invalid_code(client: TestClient, monkeypatch):
    """Test connection request with an invalid connection code."""
    from app.utils.remote_auth import remote_auth
    monkeypatch.setattr(remote_auth, "get_code", lambda: "correct-code")
    
    from app.services.instance_config_service import instance_config_service
    monkeypatch.setattr(instance_config_service, "get_instance_url", lambda db: "http://local")

    payload = {
        "identity": {
            "instance_name": "Remote",
            "url": "http://remote.com/",
            "fingerprint": "a" * 64,
            "ed25519_public_key": base64.b64encode(b"0"*32).decode("ascii"),
            "x25519_public_key": base64.b64encode(b"0"*32).decode("ascii"),
        },
        "signature": "00" * 64,
        "connection_code": "wrong-code",
    }
    response = client.post("/api/v1/remote/connection-request", json=payload)
    assert response.status_code == 400
    assert "Invalid or expired connection code" in response.json()["detail"]

@patch("app.routers.api.remote._get_found_tmp")
def test_verify_transfer_file_not_found(mock_get_tmp, authenticated_client: TestClient):
    """Test transfer verification when temp file is not found."""
    mock_get_tmp.side_effect = HTTPException(status_code=404, detail="Temporary file not found")

    data = {"relative_path": "missing.txt", "remote_path_id": 1, "checksum": "hash"}
    response = authenticated_client.post("/api/v1/remote/verify-transfer", json=data)
    assert response.status_code == 404

def test_get_transfer_status_not_found(authenticated_client: TestClient):
    """Test getting transfer status for non-existent path."""
    response = authenticated_client.get("/api/v1/remote/transfer-status?relative_path=none.txt&remote_path_id=9999&storage_type=hot")
    assert response.status_code == 404
