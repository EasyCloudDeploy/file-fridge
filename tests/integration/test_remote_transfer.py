
import asyncio
import base64
import json
import os
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
import aiofiles
from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileStatus,
    FileTransferStrategy,
    MonitoredPath,
    RemoteConnection,
    RemoteTransferJob,
    StorageType,
    TrustStatus,
    TransferStatus,
)
from app.services.remote_transfer_service import remote_transfer_service, get_transfer_timeouts, MAX_RETRIES


# --- Fixtures ---

@pytest.fixture
def local_paths(tmp_path):
    local_hot = tmp_path / "local_hot"
    local_cold = tmp_path / "local_cold"
    local_hot.mkdir()
    local_cold.mkdir()
    return local_hot, local_cold

@pytest.fixture
def remote_paths(tmp_path):
    remote_hot = tmp_path / "remote_hot"
    remote_cold = tmp_path / "remote_cold"
    remote_hot.mkdir()
    remote_cold.mkdir()
    return remote_hot, remote_cold


@pytest.fixture
def local_monitored_path(db_session: Session, local_paths):
    hot_path, cold_path = local_paths
    cold_loc = ColdStorageLocation(name="LocalCold", path=str(cold_path))
    db_session.add(cold_loc)
    db_session.commit()
    db_session.refresh(cold_loc)

    path = MonitoredPath(name="LocalMonitored", source_path=str(hot_path))
    path.storage_locations.append(cold_loc)
    db_session.add(path)
    db_session.commit()
    db_session.refresh(path)
    return path


@pytest.fixture
def remote_monitored_path(db_session: Session, remote_paths):
    hot_path, cold_path = remote_paths
    cold_loc = ColdStorageLocation(name="RemoteCold", path=str(cold_path))
    db_session.add(cold_loc)
    db_session.commit()
    db_session.refresh(cold_loc)

    path = MonitoredPath(name="RemoteMonitored", source_path=str(hot_path))
    path.storage_locations.append(cold_loc)
    db_session.add(path)
    db_session.commit()
    db_session.refresh(path)
    return path


@pytest.fixture
def local_connection(db_session: Session):
    conn = RemoteConnection(
        name="LocalToRemote",
        url="http://remote-mock.com",
        remote_fingerprint="remote_fingerprint_abc",
        remote_ed25519_public_key="3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=",
        remote_x25519_public_key="rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=",
        trust_status=TrustStatus.TRUSTED,
        transfer_mode="BIDIRECTIONAL",
        remote_transfer_mode="BIDIRECTIONAL",
    )
    db_session.add(conn)
    db_session.commit()
    db_session.refresh(conn)
    return conn


@pytest.fixture
def file_inventory_local(db_session: Session, local_monitored_path, local_paths):
    hot_path, _ = local_paths
    file_path = hot_path / "local_test_file.txt"
    file_path.write_text("This is a test file for remote transfer.")
    file_obj = FileInventory(
        path_id=local_monitored_path.id,
        file_path=str(file_path),
        storage_type=StorageType.HOT,
        file_size=file_path.stat().st_size,
        file_mtime=datetime.now(timezone.utc),
        file_atime=datetime.now(timezone.utc),
        file_ctime=datetime.now(timezone.utc),
        status=FileStatus.ACTIVE,
        checksum="local_checksum",
    )
    db_session.add(file_obj)
    db_session.commit()
    db_session.refresh(file_obj)
    return file_obj


# --- Tests ---

@respx.mock
async def test_push_transfer_success(
    db_session: Session,
    local_monitored_path,
    remote_monitored_path,
    local_connection,
    file_inventory_local,
    remote_paths,
):
    # Mock remote endpoints
    respx.get(url__regex=r".*/transfer-status.*").mock(return_value=httpx.Response(200, json={"size": 0, "status": "not_found"}))
    respx.post(url__regex=r".*/receive").mock(return_value=httpx.Response(200, json={"status": "success"}))
    respx.post(url__regex=r".*/verify-transfer").mock(return_value=httpx.Response(200, json={"status": "success"}))

    mock_session = MagicMock(wraps=db_session)
    mock_session.close = MagicMock()
    
    with patch("app.services.remote_transfer_service.SessionLocal", return_value=mock_session):
        job = remote_transfer_service.create_transfer_job(
            db_session, file_inventory_local.id, local_connection.id, remote_monitored_path.id
        )
        await remote_transfer_service.run_transfer(job.id)
        
        db_session.refresh(job)
        assert job.status == TransferStatus.COMPLETED


@respx.mock
async def test_pull_transfer_success(
    db_session: Session,
    local_monitored_path,
    local_connection,
    authenticated_client,
):
    # Mock remote serve-transfer endpoint
    respx.post(url__regex=r".*/serve-transfer").mock(return_value=httpx.Response(200, json={"status": "accepted", "job_id": "remote_pull_job_1"}))
    
    pull_data = {
        "remote_file_inventory_id": 99,
        "remote_connection_id": local_connection.id,
        "local_monitored_path_id": local_monitored_path.id,
        "strategy": "COPY",
    }
    
    response = authenticated_client.post("/api/v1/remote/pull", json=pull_data)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


@respx.mock
async def test_resumable_transfer(
    db_session: Session,
    local_monitored_path,
    remote_monitored_path,
    local_connection,
    file_inventory_local,
    remote_paths,
):
    # Mock partial remote status
    respx.get(url__regex=r".*/transfer-status.*").mock(return_value=httpx.Response(200, json={"size": 10, "status": "partial"}))
    respx.post(url__regex=r".*/receive").mock(return_value=httpx.Response(200, json={"status": "success"}))
    respx.post(url__regex=r".*/verify-transfer").mock(return_value=httpx.Response(200, json={"status": "success"}))

    mock_session = MagicMock(wraps=db_session)
    mock_session.close = MagicMock()
    
    with patch("app.services.remote_transfer_service.SessionLocal", return_value=mock_session):
        job = remote_transfer_service.create_transfer_job(
            db_session, file_inventory_local.id, local_connection.id, remote_monitored_path.id
        )
        await remote_transfer_service.run_transfer(job.id)
        
        db_session.refresh(job)
        assert job.status == TransferStatus.COMPLETED


@respx.mock
async def test_checksum_mismatch_rollback(
    db_session: Session,
    local_monitored_path,
    remote_monitored_path,
    local_connection,
    file_inventory_local,
):
    respx.get(url__regex=r".*/transfer-status.*").mock(return_value=httpx.Response(200, json={"size": 0, "status": "not_found"}))
    respx.post(url__regex=r".*/receive").mock(return_value=httpx.Response(200, json={"status": "success"}))
    respx.post(url__regex=r".*/verify-transfer").mock(return_value=httpx.Response(422))

    mock_session = MagicMock(wraps=db_session)
    mock_session.close = MagicMock()
    
    with patch("app.services.remote_transfer_service.SessionLocal", return_value=mock_session):
        job = remote_transfer_service.create_transfer_job(
            db_session, file_inventory_local.id, local_connection.id, remote_monitored_path.id
        )
        await remote_transfer_service.run_transfer(job.id)
        
        db_session.refresh(job)
        assert job.status == TransferStatus.FAILED


@respx.mock
async def test_move_strategy_cleanup(
    db_session: Session,
    local_monitored_path,
    remote_monitored_path,
    local_connection,
    file_inventory_local,
):
    respx.get(url__regex=r".*/transfer-status.*").mock(return_value=httpx.Response(200, json={"size": 0, "status": "not_found"}))
    respx.post(url__regex=r".*/receive").mock(return_value=httpx.Response(200, json={"status": "success"}))
    respx.post(url__regex=r".*/verify-transfer").mock(return_value=httpx.Response(200, json={"status": "success"}))

    mock_session = MagicMock(wraps=db_session)
    mock_session.close = MagicMock()
    
    with patch("app.services.remote_transfer_service.SessionLocal", return_value=mock_session):
        job = remote_transfer_service.create_transfer_job(
            db_session, file_inventory_local.id, local_connection.id, remote_monitored_path.id,
            strategy=FileTransferStrategy.MOVE
        )
        source_path = Path(file_inventory_local.file_path)
        assert source_path.exists()
        
        await remote_transfer_service.run_transfer(job.id)
        
        db_session.refresh(job)
        assert job.status == TransferStatus.COMPLETED
        assert not source_path.exists()


@respx.mock
async def test_encryption_headers_sent(
    db_session: Session,
    local_monitored_path,
    remote_monitored_path,
    local_connection,
    file_inventory_local,
):
    # Ensure URL is HTTP to trigger encryption
    local_connection.url = "http://remote-mock.com"
    db_session.commit()

    respx.get(url__regex=r".*/transfer-status.*").mock(return_value=httpx.Response(200, json={"size": 0, "status": "not_found"}))
    respx.post(url__regex=r".*/receive").mock(return_value=httpx.Response(200, json={"status": "success"}))
    respx.post(url__regex=r".*/verify-transfer").mock(return_value=httpx.Response(200, json={"status": "success"}))

    mock_session = MagicMock(wraps=db_session)
    mock_session.close = MagicMock()
    
    with patch("app.services.remote_transfer_service.SessionLocal", return_value=mock_session):
        with patch("app.services.remote_transfer_service.RemoteTransferService._perform_ecdh_key_exchange") as mock_ecdh:
            mock_ecdh.return_value = ("ephemeral_pub_key", b"a" * 32)
            
            job = remote_transfer_service.create_transfer_job(
                db_session, file_inventory_local.id, local_connection.id, remote_monitored_path.id
            )
            await remote_transfer_service.run_transfer(job.id)
            
            db_session.refresh(job)
            assert job.status == TransferStatus.COMPLETED
            mock_ecdh.assert_called_once()


@respx.mock
async def test_network_failure_retries_and_fails(
    db_session: Session,
    local_monitored_path,
    remote_monitored_path,
    local_connection,
    file_inventory_local,
):
    # Mock failure
    respx.get(url__regex=r".*/transfer-status.*").mock(side_effect=httpx.ConnectError("Down"))

    mock_session = MagicMock(wraps=db_session)
    mock_session.close = MagicMock()
    
    with patch("app.services.remote_transfer_service.SessionLocal", return_value=mock_session):
        with patch("app.services.remote_transfer_service.MAX_RETRIES", 2):
            job = remote_transfer_service.create_transfer_job(
                db_session, file_inventory_local.id, local_connection.id, remote_monitored_path.id
            )
            await remote_transfer_service.run_transfer(job.id)
            
            db_session.refresh(job)
            assert job.status == TransferStatus.FAILED
