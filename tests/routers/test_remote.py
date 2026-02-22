import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    MonitoredPath,
    RemoteConnection,
    RemoteTransferJob,
    TransferDirection,
    TransferMode,
    TrustStatus,
    ColdStorageLocation,
)
from app.schemas import RemoteConnectionCreate, RemoteTransferJob as RemoteTransferJobSchema

# Assume authenticated_client, monitored_path_factory, storage_location fixtures are available.


@pytest.fixture
def remote_connection_factory(db_session: Session):
    """Factory for RemoteConnection objects."""

    def _factory(
        name: str = "Test Remote",
        url: str = "http://remote.example.com",
        fingerprint: str = "testfingerprint",
        trust_status: TrustStatus = TrustStatus.TRUSTED,
        remote_transfer_mode: TransferMode = TransferMode.BIDIRECTIONAL,
        effective_bidirectional: bool = True,
    ):
        conn = RemoteConnection(
            name=name,
            url=url,
            remote_fingerprint=fingerprint,
            remote_ed25519_public_key="pubkey",
            remote_x25519_public_key="xpubkey",
            trust_status=trust_status,
            remote_transfer_mode=remote_transfer_mode,
            effective_bidirectional=effective_bidirectional,
        )
        db_session.add(conn)
        db_session.commit()
        db_session.refresh(conn)
        return conn

    return _factory


@pytest.fixture
def remote_transfer_job_factory(
    db_session: Session, remote_connection_factory, monitored_path_factory
):
    """Factory for RemoteTransferJob objects."""

    def _factory(
        file_inventory_id: int = 1,
        remote_connection: RemoteConnection = None,
        remote_monitored_path: MonitoredPath = None,
        direction: TransferDirection = TransferDirection.PUSH,
        status: str = "PENDING",
        file_name: str = "test_file.txt",
        file_size: int = 1024,
    ):
        if remote_connection is None:
            remote_connection = remote_connection_factory()
        if remote_monitored_path is None:
            remote_monitored_path = monitored_path_factory("Remote Path", "/remote/path")

        job = RemoteTransferJob(
            file_inventory_id=file_inventory_id,
            remote_connection_id=remote_connection.id,
            remote_monitored_path_id=remote_monitored_path.id,
            direction=direction,
            status=status,
            file_name=file_name,
            file_size=file_size,
            checksum="testchecksum",
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job

    return _factory


# Mock the verify_remote_signature dependency for all tests in this file
@pytest.fixture(autouse=True)
def mock_verify_remote_signature():
    with patch("app.routers.api.remote.verify_remote_signature", new_callable=AsyncMock) as mock:
        # Default behavior: return a trusted connection
        mock.return_value = MagicMock(
            spec=RemoteConnection,
            name="mock_remote_connection",
            id=1,
            remote_fingerprint="mockfingerprint",
            trust_status=TrustStatus.TRUSTED,
            effective_bidirectional=True,
            remote_ed25519_public_key="remote_pubkey",
        )
        yield mock


# Mock get_signed_headers for all tests in this file
@pytest.fixture(autouse=True)
def mock_get_signed_headers():
    with patch("app.routers.api.remote.get_signed_headers", new_callable=AsyncMock) as mock:
        mock.return_value = {"Authorization": "Bearer signed-token"}
        yield mock


# ==================================
# Connection Management Endpoints
# ==================================


@patch(
    "app.services.instance_config_service.instance_config_service.get_instance_url",
    return_value="http://localhost",
)
def test_get_remote_status_configured(mock_get_url, authenticated_client: TestClient):
    """Test get_remote_status when configured."""
    response = authenticated_client.get("/api/v1/remote/status")
    assert response.status_code == 200
    assert response.json()["configured"] is True


@patch(
    "app.services.remote_connection_service.remote_connection_service.get_remote_identity",
    new_callable=AsyncMock,
)
@patch(
    "app.services.remote_connection_service.remote_connection_service.initiate_connection",
    new_callable=AsyncMock,
)
@patch("app.utils.remote_auth.remote_auth.get_code_with_expiry")
@patch(
    "app.services.instance_config_service.instance_config_service.get_instance_url",
    return_value="http://localhost",
)
def test_connect_with_code_success(
    mock_get_url,
    mock_get_code,
    mock_initiate_connection,
    mock_get_remote_identity,
    authenticated_client: TestClient,
    db_session: Session,
):
    """Test successful connection with a code."""
    mock_get_code.return_value = ("testcode", 3600)
    mock_get_remote_identity.return_value = {
        "instance_name": "Remote",
        "fingerprint": "remote_fingerprint",
        "url": "http://remote.com",
    }
    mock_initiate_connection.return_value = RemoteConnection(
        id=1,
        name="Remote",
        remote_fingerprint="remote_fingerprint",
        trust_status="PENDING",
        url="http://remote.com",
    )

    connection_data = RemoteConnectionCreate(
        name="Remote",
        url="http://remote.com",
        connection_code="testcode",
        transfer_mode=TransferMode.BIDIRECTIONAL,
    )
    response = authenticated_client.post(
        "/api/v1/remote/connect", json=connection_data.model_dump()
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Remote"
    mock_get_remote_identity.assert_called_once_with("http://remote.com")
    mock_initiate_connection.assert_called_once()


@patch(
    "app.services.remote_connection_service.remote_connection_service.delete_connection",
    new_callable=AsyncMock,
)
def test_delete_connection_success(
    mock_delete_connection, authenticated_client: TestClient, remote_connection_factory
):
    """Test successful deletion of a connection."""
    conn = remote_connection_factory()
    mock_delete_connection.return_value = True

    response = authenticated_client.delete(f"/api/v1/remote/connections/{conn.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_delete_connection.assert_called_once_with(ANY, conn.id)


@patch("app.services.remote_connection_service.remote_connection_service.trust_connection")
def test_trust_connection_success(
    mock_trust_connection, authenticated_client: TestClient, remote_connection_factory
):
    """Test successful trusting of a connection."""
    conn = remote_connection_factory(trust_status=TrustStatus.PENDING)
    mock_trust_connection.return_value = conn

    response = authenticated_client.post(f"/api/v1/remote/connections/{conn.id}/trust")
    assert response.status_code == 200
    assert response.json()["trust_status"] == TrustStatus.TRUSTED.value
    mock_trust_connection.assert_called_once_with(ANY, conn.id)


# ==================================
# Transfer Initiation Endpoints
# ==================================


@patch("app.services.remote_transfer_service.remote_transfer_service.create_transfer_job")
def test_migrate_file_success(
    mock_create_job,
    authenticated_client: TestClient,
    remote_connection_factory,
    monitored_path_factory,
):
    """Test successful initiation of a file migration."""
    conn = remote_connection_factory()
    path = monitored_path_factory("Local Path", "/local/path")
    mock_create_job.return_value = RemoteTransferJobSchema(
        id=1,
        file_inventory_id=1,
        remote_connection_id=conn.id,
        remote_monitored_path_id=path.id,
        direction="PUSH",
        status="PENDING",
        file_name="test.txt",
        file_size=100,
    )

    migration_data = {
        "file_inventory_id": 1,
        "remote_connection_id": conn.id,
        "remote_monitored_path_id": path.id,
    }
    response = authenticated_client.post("/api/v1/remote/migrate", json=migration_data)

    assert response.status_code == 200
    assert response.json()["id"] == 1
    mock_create_job.assert_called_once()


@patch("app.services.remote_transfer_service.remote_transfer_service.create_transfer_job")
def test_bulk_migrate_files_success(
    mock_create_job,
    authenticated_client: TestClient,
    remote_connection_factory,
    monitored_path_factory,
):
    """Test successful bulk migration of files."""
    conn = remote_connection_factory()
    path = monitored_path_factory("Local Path", "/local/path")
    mock_create_job.return_value = MagicMock(id=1)  # mock the job object for the loop

    migration_data = {
        "file_ids": [1, 2],
        "remote_connection_id": conn.id,
        "remote_monitored_path_id": path.id,
        "strategy": "copy",
        "conflict_resolution": "overwrite",
    }
    response = authenticated_client.post("/api/v1/remote/migrate/bulk", json=migration_data)

    assert response.status_code == 200
    assert response.json()["successful"] == 2
    assert response.json()["failed"] == 0
    assert mock_create_job.call_count == 2


# ==================================
# Transfer Monitoring Endpoints
# ==================================


def test_list_transfers(authenticated_client: TestClient, remote_transfer_job_factory):
    """Test listing all remote transfer jobs."""
    remote_transfer_job_factory(file_name="job1.txt")
    remote_transfer_job_factory(file_name="job2.txt")

    response = authenticated_client.get("/api/v1/remote/transfers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["file_name"] == "job2.txt"  # Sorted desc by id


@patch(
    "app.services.remote_transfer_service.remote_transfer_service.cancel_transfer",
    return_value=True,
)
def test_cancel_transfer_success(
    mock_cancel_transfer, authenticated_client: TestClient, remote_transfer_job_factory
):
    """Test successful cancellation of a transfer job."""
    job = remote_transfer_job_factory(status="PENDING")

    response = authenticated_client.post(f"/api/v1/remote/transfers/{job.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_cancel_transfer.assert_called_once_with(ANY, job.id)


@patch("app.services.remote_transfer_service.remote_transfer_service.cancel_transfer")
def test_bulk_cancel_transfers(
    mock_cancel_transfer, authenticated_client: TestClient, remote_transfer_job_factory
):
    """Test bulk cancellation of transfers."""
    job1 = remote_transfer_job_factory(status="PENDING")
    job2 = remote_transfer_job_factory(status="IN_PROGRESS")
    mock_cancel_transfer.side_effect = [True, False]  # job1 succeeds, job2 fails

    response = authenticated_client.post(
        "/api/v1/remote/transfers/bulk/cancel",
        json=[job1.id, job2.id],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == [job1.id]
    assert data["failed"] == [job2.id]
    assert mock_cancel_transfer.call_count == 2


@patch("app.services.instance_config_service.instance_config_service.get_config_info")
def test_get_instance_config(mock_get_config_info, authenticated_client: TestClient):
    """Test retrieving instance config."""
    mock_get_config_info.return_value = {"instance_url": "http://test.com", "instance_name": "Test"}
    response = authenticated_client.get("/api/v1/remote/config")
    assert response.status_code == 200
    assert response.json()["instance_url"] == "http://test.com"


@patch("app.services.instance_config_service.instance_config_service.set_instance_url")
@patch("app.services.instance_config_service.instance_config_service.set_instance_name")
@patch(
    "app.services.instance_config_service.instance_config_service.get_config_info",
    return_value={"instance_url": "http://new.com"},
)
def test_update_instance_config(
    mock_get_config_info, mock_set_name, mock_set_url, authenticated_client: TestClient
):
    """Test updating instance config."""
    response = authenticated_client.post(
        "/api/v1/remote/config",
        json={"instance_url": "http://new.com", "instance_name": "New Name"},
    )
    assert response.status_code == 200
    assert response.json()["instance_url"] == "http://new.com"
    mock_set_url.assert_called_once_with(ANY, "http://new.com")
    mock_set_name.assert_called_once_with(ANY, "New Name")


@patch(
    "app.services.instance_config_service.instance_config_service.get_instance_url",
    return_value="http://localhost",
)
@patch(
    "app.services.identity_service.identity_service.get_instance_fingerprint",
    return_value="fingerprint",
)
@patch(
    "app.services.identity_service.identity_service.get_signing_public_key_str",
    return_value="signing_key",
)
@patch(
    "app.services.identity_service.identity_service.get_kx_public_key_str", return_value="kx_key"
)
def test_get_public_identity(
    mock_kx_key, mock_signing_key, mock_fingerprint, mock_get_url, authenticated_client: TestClient
):
    """Test retrieving public identity."""
    response = authenticated_client.get("/api/v1/remote/identity")
    assert response.status_code == 200
    assert response.json()["fingerprint"] == "fingerprint"


@patch("app.utils.remote_auth.remote_auth.get_code_with_expiry", return_value=("testcode", 3600))
@patch(
    "app.services.instance_config_service.instance_config_service.get_instance_url",
    return_value="http://localhost",
)
def test_get_connection_code(mock_get_url, mock_get_code, authenticated_client: TestClient):
    """Test retrieving connection code."""
    response = authenticated_client.get("/api/v1/remote/connection-code")
    assert response.status_code == 200
    assert response.json()["code"] == "testcode"
    assert response.json()["expires_in_seconds"] == 3600


@patch(
    "app.services.remote_connection_service.remote_connection_service.get_remote_identity",
    new_callable=AsyncMock,
    return_value={
        "instance_name": "Remote",
        "fingerprint": "remote_fingerprint",
        "url": "http://remote.com",
    },
)
def test_fetch_remote_identity(mock_get_remote_identity, authenticated_client: TestClient):
    """Test fetching remote identity."""
    response = authenticated_client.post(
        "/api/v1/remote/connections/fetch-identity", json={"url": "http://remote.com"}
    )
    assert response.status_code == 200
    assert response.json()["fingerprint"] == "remote_fingerprint"
    mock_get_remote_identity.assert_called_once_with("http://remote.com")


@patch("app.services.remote_connection_service.remote_connection_service.handle_connection_request")
def test_handle_connection_request(mock_handle_request, authenticated_client: TestClient):
    """Test handling incoming connection request."""
    mock_handle_request.return_value = {"status": "accepted"}
    response = authenticated_client.post("/api/v1/remote/connection-request", json={"some": "data"})
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    mock_handle_request.assert_called_once()


@patch("app.services.remote_connection_service.remote_connection_service.get_connection")
@patch(
    "app.services.remote_connection_service.remote_connection_service.notify_transfer_mode_change",
    new_callable=AsyncMock,
)
def test_update_connection_success(
    mock_notify_change,
    mock_get_connection,
    authenticated_client: TestClient,
    remote_connection_factory,
):
    """Test updating a remote connection."""
    conn = remote_connection_factory(name="Old Name", trust_status=TrustStatus.TRUSTED)
    mock_get_connection.return_value = conn

    response = authenticated_client.patch(
        f"/api/v1/remote/connections/{conn.id}",
        json={"name": "New Name", "transfer_mode": "PULL"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["transfer_mode"] == "PULL"
    mock_notify_change.assert_called_once()


@patch("app.services.remote_connection_service.remote_connection_service.reject_connection")
def test_reject_connection_success(
    mock_reject_connection, authenticated_client: TestClient, remote_connection_factory
):
    """Test rejecting a remote connection."""
    conn = remote_connection_factory(trust_status=TrustStatus.PENDING)
    mock_reject_connection.return_value = conn

    response = authenticated_client.post(f"/api/v1/remote/connections/{conn.id}/reject")
    assert response.status_code == 200
    assert response.json()["trust_status"] == TrustStatus.REJECTED.value
    mock_reject_connection.assert_called_once_with(ANY, conn.id)


@patch(
    "app.services.remote_connection_service.remote_connection_service.handle_terminate_connection"
)
def test_terminate_connection(
    mock_terminate_connection, authenticated_client: TestClient, mock_verify_remote_signature
):
    """Test terminating a connection (inter-instance endpoint)."""
    # mock_verify_remote_signature is already configured to return a trusted connection
    response = authenticated_client.post("/api/v1/remote/terminate-connection")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_terminate_connection.assert_called_once_with(
        ANY, mock_verify_remote_signature.return_value.remote_fingerprint
    )


@patch("httpx.AsyncClient.get", new_callable=AsyncMock)
@patch("app.services.remote_connection_service.remote_connection_service.get_connection")
def test_get_remote_paths(
    mock_get_connection, mock_httpx_get, authenticated_client: TestClient, remote_connection_factory
):
    """Test fetching remote paths from a connected instance."""
    conn = remote_connection_factory()
    mock_get_connection.return_value = conn
    mock_httpx_get.return_value = MagicMock(
        status_code=200, json=lambda: [{"id": 1, "name": "RemotePath"}]
    )

    response = authenticated_client.get(f"/api/v1/remote/connections/{conn.id}/paths")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "RemotePath"
    mock_httpx_get.assert_called_once()


@patch(
    "app.services.remote_transfer_service.remote_transfer_service.cancel_transfer",
    return_value=True,
)
def test_delete_transfer_job(
    mock_cancel_transfer, authenticated_client: TestClient, remote_transfer_job_factory, db_session
):
    """Test deleting a transfer job."""
    job = remote_transfer_job_factory(status="COMPLETED")

    response = authenticated_client.delete(f"/api/v1/remote/transfers/{job.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify job is deleted from DB
    deleted_job = db_session.query(RemoteTransferJob).filter(RemoteTransferJob.id == job.id).first()
    assert deleted_job is None


@patch(
    "app.services.remote_transfer_service.remote_transfer_service.cancel_transfer",
    return_value=True,
)
def test_bulk_delete_transfers(
    mock_cancel_transfer, authenticated_client: TestClient, remote_transfer_job_factory, db_session
):
    """Test bulk deleting transfer jobs."""
    job1 = remote_transfer_job_factory(status="COMPLETED")
    job2 = remote_transfer_job_factory(status="FAILED")
    job3 = remote_transfer_job_factory(status="PENDING")  # Should fail to delete

    response = authenticated_client.post(
        f"/api/v1/remote/transfers/bulk/delete", json=[job1.id, job2.id, job3.id]
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["succeeded"]) == 2
    assert len(data["failed"]) == 1
    assert data["failed"][0]["id"] == job3.id

    # Verify in DB
    assert (
        db_session.query(RemoteTransferJob).filter(RemoteTransferJob.id == job1.id).first() is None
    )
    assert (
        db_session.query(RemoteTransferJob).filter(RemoteTransferJob.id == job2.id).first() is None
    )
    assert (
        db_session.query(RemoteTransferJob).filter(RemoteTransferJob.id == job3.id).first()
        is not None
    )


@patch(
    "app.services.remote_transfer_service.remote_transfer_service.run_transfer",
    new_callable=AsyncMock,
)
@patch("app.services.scheduler.scheduler_service.trigger_scan")
@patch("app.utils.disk_validator.disk_space_validator.validate_disk_space_direct")
@patch(
    "app.routers.api.remote._decrypt_chunk",
    new_callable=AsyncMock,
    return_value=b"decompressed_data",
)
@patch(
    "app.routers.api.remote._decompress_chunk",
    new_callable=AsyncMock,
    return_value=b"decompressed_data",
)
@patch("app.routers.api.remote.anyio.to_thread.run_sync")
@patch("aiofiles.open", new_callable=MagicMock)
def test_receive_chunk(
    mock_aiofiles_open,
    mock_run_sync,
    mock_decompress_chunk,
    mock_decrypt_chunk,
    mock_disk_validator,
    mock_trigger_scan,
    mock_run_transfer,
    authenticated_client: TestClient,
    monitored_path_factory,
    tmp_path,
    mock_verify_remote_signature,
    db_session,
):
    """Test receiving a file chunk via /receive."""
    mock_run_sync.side_effect = lambda func, *args, **kwargs: func(
        *args, **kwargs
    )  # Allow some passthrough

    monitored_path = monitored_path_factory("ReceivePath", str(tmp_path / "hot_receive"))
    # The _get_base_directory function expects a path.storage_locations
    monitored_path.storage_locations.append(
        ColdStorageLocation(name="Cold", path=str(tmp_path / "cold_receive"))
    )
    db_session.add(monitored_path)
    db_session.commit()
    db_session.refresh(monitored_path)

    # Mock the returned RemoteConnection from verify_remote_signature
    mock_verify_remote_signature.return_value = MagicMock(
        spec=RemoteConnection,
        name="mock_remote_connection",
        id=1,
        remote_fingerprint="mockfingerprint",
        trust_status=TrustStatus.TRUSTED,
        effective_bidirectional=True,
        remote_ed25519_public_key="remote_pubkey",
    )

    mock_file_writer = MagicMock()
    mock_aiofiles_open.return_value.__aenter__.return_value = mock_file_writer

    headers = {
        "X-Chunk-Index": "0",
        "X-Relative-Path": "test_file.txt",
        "X-Remote-Path-ID": str(monitored_path.id),
        "X-Storage-Type": "hot",
        "X-Encryption-Nonce": "",
        "X-Ephemeral-Public-Key": "",
        "X-Job-ID": "job123",
        "X-Is-Final": "true",
        "X-Fingerprint": "mockfingerprint",
        "X-Timestamp": str(int(time.time())),
        "X-Nonce": "randomnonce",
        "X-Signature": "mocksignature",
        "X-File-Size": "100",
    }
    body = b"encrypted_compressed_data"

    response = authenticated_client.post("/api/v1/remote/receive", headers=headers, content=body)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_decrypt_chunk.assert_called_once()
    mock_decompress_chunk.assert_called_once()
    mock_file_writer.write.assert_called_once_with(b"decompressed_data")


@patch("app.routers.api.remote.scheduler_service.trigger_scan")
@patch(
    "app.routers.api.remote.file_metadata_extractor.compute_sha256",
    new_callable=AsyncMock,
    return_value="computed_checksum",
)
@patch("pathlib.Path.rename")
@patch("pathlib.Path.unlink")
@patch("app.routers.api.remote._get_found_tmp", new_callable=AsyncMock)
def test_verify_transfer_success(
    mock_get_found_tmp,
    mock_unlink,
    mock_rename,
    mock_compute_sha256,
    mock_trigger_scan,
    authenticated_client: TestClient,
    tmp_path,
    mock_verify_remote_signature,
):
    """Test successful transfer verification."""
    tmp_file = tmp_path / "test.txt.fftmp"
    tmp_file.touch()
    mock_get_found_tmp.return_value = tmp_file

    data = {
        "relative_path": "test.txt",
        "remote_path_id": 1,
        "checksum": "computed_checksum",
    }
    response = authenticated_client.post("/api/v1/remote/verify-transfer", json=data)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_rename.assert_called_once()
    mock_compute_sha256.assert_called_once()
    mock_trigger_scan.assert_called_once_with(1)


@patch("app.routers.api.remote._get_base_directory", return_value="/tmp/base")
@patch("pathlib.Path.exists", side_effect=[True, False])  # Final path exists, tmp path does not
@patch("pathlib.Path.stat")
def test_get_transfer_status_completed(
    mock_stat,
    mock_path_exists,
    mock_get_base_dir,
    authenticated_client: TestClient,
    monitored_path_factory,
):
    """Test getting transfer status for a completed transfer."""
    monitored_path_factory("TestPath", "/tmp/base")
    mock_stat.return_value = MagicMock(st_size=1024)

    response = authenticated_client.get(
        "/api/v1/remote/transfer-status?relative_path=file.txt&remote_path_id=1&storage_type=hot"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["size"] == 1024


@patch("app.routers.api.remote.scheduler_service.trigger_scan")
@patch(
    "app.services.remote_transfer_service.remote_transfer_service.cancel_transfer",
    return_value=True,
)
def test_bulk_retry_transfers(
    mock_cancel,
    mock_trigger,
    authenticated_client: TestClient,
    remote_transfer_job_factory,
    db_session,
):
    """Test bulk retrying of transfers."""
    job1 = remote_transfer_job_factory(status="FAILED")
    job2 = remote_transfer_job_factory(status="COMPLETED")  # Should fail to retry

    response = authenticated_client.post(
        f"/api/v1/remote/transfers/bulk/retry", json={"job_ids": [job1.id, job2.id]}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == [job1.id]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["id"] == job2.id
    assert db_session.query(RemoteTransferJob).get(job1.id).status == "PENDING"
    assert db_session.query(RemoteTransferJob).get(job2.id).status == "COMPLETED"  # Not retried


@patch("app.services.remote_transfer_service.remote_transfer_service.create_transfer_job")
@patch("app.services.remote_connection_service.remote_connection_service.get_connection")
@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
def test_pull_file_success(
    mock_httpx_post,
    mock_get_connection,
    mock_create_transfer_job,
    authenticated_client: TestClient,
    remote_connection_factory,
    monitored_path_factory,
    db_session,
):
    """Test successful pull file request."""
    conn = remote_connection_factory(effective_bidirectional=True)
    local_path = monitored_path_factory("LocalPath", "/local/path")
    mock_get_connection.return_value = conn
    mock_httpx_post.return_value = MagicMock(
        status_code=200, json=lambda: {"status": "accepted", "job_id": "remote_job_1"}
    )
    mock_create_transfer_job.return_value = MagicMock(id=1)

    pull_data = {
        "remote_file_inventory_id": 10,
        "remote_connection_id": conn.id,
        "local_monitored_path_id": local_path.id,
        "strategy": "copy",
    }

    response = authenticated_client.post("/api/v1/remote/pull", json=pull_data)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["remote_job_id"] == "remote_job_1"
    mock_httpx_post.assert_called_once()


@patch(
    "app.routers.api.remote.db.query"
)  # Mock db.query to avoid actual DB calls for MonitoredPath
def test_exposed_paths(mock_db_query, authenticated_client: TestClient, monitored_path_factory):
    """Test the /exposed-paths endpoint (inter-instance)."""
    # Create a mock for the MonitoredPath objects that db.query.filter.all() would return
    mock_path1 = MagicMock(id=1, name="Path A", enabled=True)
    mock_path2 = MagicMock(id=2, name="Path B", enabled=True)

    # Configure the mock db.query to return these mock paths
    mock_db_query.return_value.filter.return_value.all.return_value = [mock_path1, mock_path2]

    response = authenticated_client.get("/api/v1/remote/exposed-paths")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["name"] == "Path A"


@patch("app.routers.api.remote.db.query")
@patch("app.routers.api.remote._get_relative_path", return_value="relative/path/file.txt")
def test_browse_remote_files(
    mock_get_relative_path, mock_db_query, authenticated_client: TestClient, monitored_path_factory
):
    """Test browsing remote files (inter-instance)."""
    # Setup mock MonitoredPath and FileInventory
    mock_path = monitored_path_factory("Browse Path", "/browse/hot")
    mock_path.id = 1  # Ensure mock path has an ID
    mock_path.enabled = True

    mock_file = MagicMock(
        id=101,
        file_path="/browse/hot/relative/path/file.txt",
        file_size=1024,
        storage_type=StorageType.HOT,
        file_mtime=datetime.now(timezone.utc),
        checksum="abc",
        file_extension=".txt",
        status=FileStatus.ACTIVE,
    )
    mock_file.storage_type.value = StorageType.HOT.value  # Mock enum value access

    # Mock db queries for path and files
    mock_db_query.return_value.filter.return_value.first.return_value = mock_path
    mock_db_query.return_value.filter.return_value.count.return_value = 1
    mock_db_query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = [
        mock_file
    ]

    response = authenticated_client.get(f"/api/v1/remote/browse-files?path_id={mock_path.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["path_name"] == "Browse Path"
    assert data["total_count"] == 1
    assert data["files"][0]["inventory_id"] == 101
    assert data["files"][0]["relative_path"] == "relative/path/file.txt"


@patch("app.services.remote_transfer_service.remote_transfer_service.create_transfer_job")
@patch(
    "app.services.remote_transfer_service.remote_transfer_service.run_transfer",
    new_callable=AsyncMock,
)
@patch("app.routers.api.remote.db.query")
def test_serve_transfer_request(
    mock_db_query,
    mock_run_transfer,
    mock_create_transfer_job,
    authenticated_client: TestClient,
):
    """Test serving a transfer request (inter-instance)."""
    # Setup mock FileInventory
    mock_file_inventory = MagicMock(id=10, file_path="/hot/file.txt")
    mock_db_query.return_value.filter.return_value.first.return_value = mock_file_inventory

    # Setup mock transfer job
    mock_transfer_job = MagicMock(id=1, status=TransferStatus.PENDING)
    mock_create_transfer_job.return_value = mock_transfer_job

    payload = {
        "file_inventory_id": 10,
        "remote_monitored_path_id": 1,
        "strategy": "copy",
    }
    response = authenticated_client.post("/api/v1/remote/serve-transfer", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["job_id"] == 1
    mock_create_transfer_job.assert_called_once()
    mock_run_transfer.assert_called_once_with(1)  # Ensure background task is added


@patch("app.routers.api.remote.db.query")
def test_sync_transfer_mode(mock_db_query, authenticated_client: TestClient):
    """Test syncing transfer mode (inter-instance)."""
    mock_remote_connection = MagicMock(
        remote_transfer_mode=TransferMode.PUSH, effective_bidirectional=True
    )
    # The global mock_verify_remote_signature returns a MagicMock connection,
    # we need to ensure its remote_transfer_mode and effective_bidirectional are updated
    mock_remote_connection.remote_transfer_mode = TransferMode.PUSH
    mock_remote_connection.effective_bidirectional = True

    # Ensure our global mock_verify_remote_signature returns this mock connection
    # Note: In a real scenario, mock_verify_remote_signature would return the specific conn
    # related to the signature. For this test, we are just making sure the patching works.
    with patch(
        "app.routers.api.remote.verify_remote_signature", return_value=mock_remote_connection
    ):
        payload = {"transfer_mode": "PULL"}
        response = authenticated_client.post("/api/v1/remote/sync-transfer-mode", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Assert that the remote_transfer_mode was updated on the mocked object
        assert mock_remote_connection.remote_transfer_mode == TransferMode.PULL
        # The effective_bidirectional property should reflect the change
        assert mock_remote_connection.effective_bidirectional is False
