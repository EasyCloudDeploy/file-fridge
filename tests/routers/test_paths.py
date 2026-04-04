import random
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileStatus,
    ScanStatus,
    StorageType,
)
from app.services.scan_progress import scan_progress_manager


@pytest.fixture(autouse=True)
def reset_scan_progress_manager():
    """Reset in-memory scan progress state between path router tests."""
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()
    yield
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()


def _seed_live_freeze_state(db_session, monitored_path_factory, storage_location, tmp_path):
    """Create a real sparse 1GB file and partial freeze progress state."""
    hot_path = tmp_path / "hot_live"
    cold_path = tmp_path / "cold_live"
    hot_path.mkdir()
    cold_path.mkdir()

    storage_location.path = str(cold_path)
    db_session.commit()
    db_session.refresh(storage_location)
    storage_location_id = storage_location.id

    monitored_path = monitored_path_factory("Live Metrics Path", str(hot_path))
    monitored_path.operation_type = "move"
    db_session.commit()
    db_session.refresh(monitored_path)

    total_bytes = 1024 * 1024 * 1024
    source_file = hot_path / "live-freeze.bin"
    with source_file.open("wb") as handle:
        handle.truncate(total_bytes)

    inventory = FileInventory(
        path_id=monitored_path.id,
        file_path=str(source_file),
        file_size=total_bytes,
        file_mtime=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        status=FileStatus.ACTIVE,
        storage_type=StorageType.HOT,
    )
    db_session.add(inventory)
    db_session.commit()
    db_session.refresh(inventory)

    pause_bytes = random.Random(20260402).choice([16, 24, 32, 48, 64]) * 1024 * 1024
    destination_file = cold_path / source_file.name

    scan_progress_manager.start_scan(monitored_path.id, total_files=1)
    operation_id = scan_progress_manager.start_file_operation(
        monitored_path.id,
        source_file.name,
        "move_to_cold",
        total_bytes,
        file_path=str(source_file),
    )

    inventory.status = FileStatus.MIGRATING
    db_session.commit()

    chunk_size = 4 * 1024 * 1024
    bytes_transferred = 0
    with source_file.open("rb") as src_handle, destination_file.open("wb") as dest_handle:
        while bytes_transferred < pause_bytes:
            chunk = src_handle.read(min(chunk_size, pause_bytes - bytes_transferred))
            if not chunk:
                break
            dest_handle.write(chunk)
            bytes_transferred += len(chunk)

    time.sleep(0.05)
    scan_progress_manager.update_file_progress(monitored_path.id, operation_id, bytes_transferred)
    db_session.expire_all()

    return {
        "path_id": monitored_path.id,
        "inventory_id": inventory.id,
        "storage_location_id": storage_location_id,
        "destination_file": destination_file,
        "source_file": source_file,
        "operation_id": operation_id,
        "total_bytes": total_bytes,
    }


def test_list_paths(authenticated_client: TestClient, monitored_path_factory):
    """Test listing all monitored paths."""
    monitored_path_factory("Path 1", "/tmp/hot1")
    monitored_path_factory("Path 2", "/tmp/hot2")

    response = authenticated_client.get("/api/v1/paths")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Path 1"


def test_list_paths_live_freeze_metrics(
    authenticated_client: TestClient,
    db_session: Session,
    monitored_path_factory,
    storage_location: ColdStorageLocation,
    tmp_path,
):
    """Test /paths reflects real hot/cold counts during and after a live freeze."""
    state = _seed_live_freeze_state(
        db_session,
        monitored_path_factory,
        storage_location,
        tmp_path,
    )

    try:
        response = authenticated_client.get("/api/v1/paths")
        assert response.status_code == 200
        path_data = next(item for item in response.json() if item["id"] == state["path_id"])

        assert path_data["file_count"] == 1
        assert path_data["hot_file_count"] == 1
        assert path_data["cold_file_count"] == 0
        assert path_data["is_path_present"] is True

        (
            db_session.query(FileInventory)
            .filter(FileInventory.id == state["inventory_id"])
            .update(
                {
                    "storage_type": StorageType.COLD,
                    "status": FileStatus.ACTIVE,
                    "file_path": str(state["destination_file"]),
                    "cold_storage_location_id": state["storage_location_id"],
                }
            )
        )
        db_session.commit()

        response = authenticated_client.get("/api/v1/paths")
        assert response.status_code == 200
        path_data = next(item for item in response.json() if item["id"] == state["path_id"])

        assert path_data["file_count"] == 1
        assert path_data["hot_file_count"] == 0
        assert path_data["cold_file_count"] == 1
    finally:
        scan_progress_manager.complete_file_operation(
            state["path_id"],
            state["operation_id"],
            "move_to_cold",
            success=False,
            error="test cleanup",
        )
        scan_progress_manager.finish_scan(state["path_id"], status="stopped")


@pytest.mark.parametrize("seed", [5, 17, 61, 149, 409])
def test_list_paths_randomized_counts_consistency(
    authenticated_client: TestClient,
    db_session: Session,
    monitored_path_factory,
    storage_location: ColdStorageLocation,
    tmp_path,
    seed: int,
):
    """Fuzz /paths with reproducible randomized inventory state."""
    rng = random.Random(seed)
    expected_by_path_id = {}

    for index in range(rng.randint(2, 5)):
        hot_path = tmp_path / f"path_{seed}_{index}"
        monitored_path = monitored_path_factory(f"Path {seed}-{index}", str(hot_path))
        db_session.commit()
        db_session.refresh(monitored_path)

        file_count = 0
        hot_count = 0
        cold_count = 0
        for file_index in range(rng.randint(1, 6)):
            storage_type = rng.choice([StorageType.HOT, StorageType.COLD])
            status = rng.choice(
                [FileStatus.ACTIVE, FileStatus.MIGRATING, FileStatus.MOVED, FileStatus.MISSING]
            )

            if storage_type == StorageType.HOT:
                file_path = hot_path / f"file_{file_index}.bin"
                cold_storage_location_id = None
            else:
                cold_dir = Path(storage_location.path)
                file_path = cold_dir / f"file_{seed}_{index}_{file_index}.bin"
                cold_storage_location_id = storage_location.id

            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("wb") as handle:
                handle.truncate(rng.randint(1, 16) * 1024)

            db_session.add(
                FileInventory(
                    path_id=monitored_path.id,
                    file_path=str(file_path),
                    file_size=rng.randint(1, 32) * 1024,
                    file_mtime=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    status=status,
                    storage_type=storage_type,
                    cold_storage_location_id=cold_storage_location_id,
                )
            )

            file_count += 1
            if status in [FileStatus.ACTIVE, FileStatus.MIGRATING, FileStatus.MOVED]:
                if storage_type == StorageType.HOT:
                    hot_count += 1
                else:
                    cold_count += 1

        db_session.commit()
        expected_by_path_id[monitored_path.id] = {
            "file_count": file_count,
            "hot_file_count": hot_count,
            "cold_file_count": cold_count,
        }

    response = authenticated_client.get("/api/v1/paths")
    assert response.status_code == 200
    data = {item["id"]: item for item in response.json()}

    for path_id, expected in expected_by_path_id.items():
        actual = data[path_id]
        assert actual["file_count"] == expected["file_count"], f"seed={seed}, path_id={path_id}"
        assert (
            actual["hot_file_count"] == expected["hot_file_count"]
        ), f"seed={seed}, path_id={path_id}"
        assert (
            actual["cold_file_count"] == expected["cold_file_count"]
        ), f"seed={seed}, path_id={path_id}"


@patch("app.services.scheduler.scheduler_service.add_path_job")
def test_create_path(
    mock_add_job, authenticated_client: TestClient, storage_location: ColdStorageLocation, tmp_path
):
    """Test creating a new monitored path."""
    source_path = tmp_path / "new_hot"
    source_path.mkdir()

    path_data = {
        "name": "New Path",
        "source_path": str(source_path),
        "operation_type": "move",
        "check_interval_seconds": 3600,
        "max_concurrent_migrations": 5,
        "storage_location_ids": [storage_location.id],
    }

    response = authenticated_client.post("/api/v1/paths", json=path_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Path"
    assert data["source_path"] == str(source_path)
    mock_add_job.assert_called_once()


def test_create_path_invalid_source(
    authenticated_client: TestClient, storage_location: ColdStorageLocation
):
    """Test creating a path with an invalid source path."""
    path_data = {
        "name": "Invalid Path",
        "source_path": "/non/existent/path",
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    response = authenticated_client.post("/api/v1/paths", json=path_data)
    assert response.status_code == 400
    assert "Path does not exist" in response.json()["detail"]


def test_get_path(authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test retrieving a single monitored path."""
    path = monitored_path_factory("My Path", str(tmp_path / "my_hot"))

    response = authenticated_client.get(f"/api/v1/paths/{path.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Path"
    assert data["file_count"] == 0


def test_update_path(authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test updating a monitored path."""
    path = monitored_path_factory("Old Name", str(tmp_path / "old_hot"))

    update_data = {"name": "New Name"}
    response = authenticated_client.put(f"/api/v1/paths/{path.id}", json=update_data)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"


@patch("app.services.scheduler.scheduler_service.remove_path_job")
def test_delete_path(
    mock_remove_job, authenticated_client: TestClient, monitored_path_factory, tmp_path
):
    """Test deleting a monitored path."""
    path = monitored_path_factory("To Be Deleted", str(tmp_path / "delete_me"))

    response = authenticated_client.delete(f"/api/v1/paths/{path.id}")
    assert response.status_code == 200

    # Verify it's gone
    response = authenticated_client.get(f"/api/v1/paths/{path.id}")
    assert response.status_code == 404
    mock_remove_job.assert_called_once_with(path.id)


@patch("app.services.scheduler.scheduler_service.trigger_scan")
def test_trigger_scan(
    mock_trigger_scan, authenticated_client: TestClient, monitored_path_factory, tmp_path
):
    """Test triggering a scan for a path."""
    path = monitored_path_factory("Scan Me", str(tmp_path / "scan_hot"))

    response = authenticated_client.post(f"/api/v1/paths/{path.id}/scan")

    assert response.status_code == 202
    assert "Scan triggered" in response.json()["message"]
    mock_trigger_scan.assert_called_once_with(path.id)


@patch("app.services.scan_progress.scan_progress_manager.get_progress")
def test_get_scan_progress(
    mock_get_progress, authenticated_client: TestClient, monitored_path_factory, tmp_path
):
    """Test getting the scan progress for a path."""
    path = monitored_path_factory("Progress Path", str(tmp_path / "progress_hot"))
    mock_progress_data = {
        "status": "running",
        "progress": {"percent": 50},
        "current_operations": [],
    }
    mock_get_progress.return_value = mock_progress_data

    response = authenticated_client.get(f"/api/v1/paths/{path.id}/scan/progress")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["progress"]["percent"] == 50
    mock_get_progress.assert_called_once_with(path.id)


def test_get_scan_errors(
    authenticated_client: TestClient, db_session: Session, monitored_path_factory, tmp_path
):
    """Test getting scan errors for a path."""
    path = monitored_path_factory("Error Path", str(tmp_path / "error_hot"))
    path.last_scan_status = ScanStatus.FAILURE
    path.last_scan_error_log = "Disk full"
    db_session.commit()

    response = authenticated_client.get(f"/api/v1/paths/{path.id}/scan-errors")
    assert response.status_code == 200
    data = response.json()
    assert data["last_scan_status"] == ScanStatus.FAILURE.value
    assert data["last_scan_error_log"] == "Disk full"


@patch("shutil.disk_usage")
def test_get_hot_storage_stats(
    mock_disk_usage, authenticated_client: TestClient, monitored_path_factory, tmp_path
):
    """Test the /stats endpoint."""
    path = monitored_path_factory("Stats Path", str(tmp_path / "stats_hot"))
    mock_disk_usage.return_value = (1000, 500, 500)  # total, used, free

    response = authenticated_client.get("/api/v1/paths/stats")
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    # Find the stat for our test path
    stat = next((s for s in data if s["path"] == str(tmp_path / "stats_hot")), None)
    assert stat is not None
    assert stat["total_bytes"] == 1000
    assert stat["used_bytes"] == 500
    assert stat["free_bytes"] == 500
    mock_disk_usage.assert_called()


def test_create_path_duplicate_name(
    authenticated_client: TestClient, monitored_path_factory, storage_location, tmp_path
):
    """Test creating a path with a duplicate name."""
    monitored_path_factory("Existing Path", str(tmp_path / "hot1"))

    payload = {
        "name": "Existing Path",
        "source_path": str(tmp_path / "hot2"),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    # Create hot2
    Path(payload["source_path"]).mkdir()

    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


def test_delete_path_undo_operations(
    authenticated_client: TestClient, db_session, monitored_path_factory, tmp_path, monkeypatch
):
    """Test deleting a path with undo_operations=True."""
    path = monitored_path_factory("Undo Path", str(tmp_path / "hot_undo"))

    from app.services.path_reverser import PathReverser

    mock_reverse = MagicMock(return_value={"files_reversed": 1, "errors": []})
    monkeypatch.setattr(PathReverser, "reverse_path_operations", mock_reverse)

    response = authenticated_client.delete(f"/api/v1/paths/{path.id}?undo_operations=true")
    assert response.status_code == 200
    assert response.json()["files_reversed"] == 1
    mock_reverse.assert_called_once()


def test_validate_path_access_not_dir(authenticated_client: TestClient, storage_location, tmp_path):
    """Test path validation when source is a file, not a directory."""
    not_a_dir = tmp_path / "not_a_dir.txt"
    not_a_dir.write_text("file content")

    payload = {
        "name": "File Path",
        "source_path": str(not_a_dir),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"].lower()


def test_update_path_success(
    authenticated_client: TestClient, monitored_path_factory, tmp_path, storage_location
):
    """Test updating multiple fields of a monitored path."""
    path = monitored_path_factory("Original Name", str(tmp_path / "orig_hot"))
    new_hot = tmp_path / "new_hot_upd"
    new_hot.mkdir()

    payload = {
        "name": "Updated Path Name",
        "source_path": str(new_hot),
        "check_interval_seconds": 7200,
        "max_concurrent_migrations": 10,
        "enabled": False,
    }
    response = authenticated_client.put(f"/api/v1/paths/{path.id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Path Name"
    assert data["check_interval_seconds"] == 7200
    assert data["max_concurrent_migrations"] == 10
    assert data["enabled"] is False


def test_update_path_storage_locations(
    authenticated_client: TestClient, monitored_path_factory, db_session, tmp_path, storage_location
):
    """Test updating storage locations for a path."""
    path = monitored_path_factory("Multi Loc Path", str(tmp_path / "multi_hot"))

    # Create another storage location
    new_loc = ColdStorageLocation(name="Secondary Storage", path=str(tmp_path / "secondary"))
    Path(new_loc.path).mkdir()
    db_session.add(new_loc)
    db_session.commit()

    payload = {"storage_location_ids": [storage_location.id, new_loc.id]}
    response = authenticated_client.put(f"/api/v1/paths/{path.id}", json=payload)
    assert response.status_code == 200
    # Check if both are associated
    assert len(response.json()["storage_locations"]) == 2


def test_validate_path_access_not_writable(
    authenticated_client: TestClient, storage_location, tmp_path, monkeypatch
):
    """Test path validation when directory is not writable."""
    read_only_dir = tmp_path / "read_only"
    read_only_dir.mkdir()

    # Mock os.access to return False for W_OK
    import os

    original_access = os.access

    def mock_access(path, mode):
        if mode == os.W_OK and str(path) == str(read_only_dir):
            return False
        return original_access(path, mode)

    monkeypatch.setattr(os, "access", mock_access)

    payload = {
        "name": "Read Only Path",
        "source_path": str(read_only_dir),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not writable" in response.json()["detail"].lower()


def test_validate_path_access_not_readable(
    authenticated_client: TestClient, storage_location, tmp_path, monkeypatch
):
    """Test path validation when directory is not readable."""
    not_readable_dir = tmp_path / "not_readable"
    not_readable_dir.mkdir()

    import os

    original_access = os.access

    def mock_access(path, mode):
        if mode == os.R_OK and str(path) == str(not_readable_dir):
            return False
        return original_access(path, mode)

    monkeypatch.setattr(os, "access", mock_access)

    payload = {
        "name": "Not Readable Path",
        "source_path": str(not_readable_dir),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not readable" in response.json()["detail"].lower()


def test_validate_path_access_not_executable(
    authenticated_client: TestClient, storage_location, tmp_path, monkeypatch
):
    """Test path validation when directory is not executable."""
    not_exec_dir = tmp_path / "not_exec"
    not_exec_dir.mkdir()

    import os

    original_access = os.access

    def mock_access(path, mode):
        if mode == os.X_OK and str(path) == str(not_exec_dir):
            return False
        return original_access(path, mode)

    monkeypatch.setattr(os, "access", mock_access)

    payload = {
        "name": "Not Exec Path",
        "source_path": str(not_exec_dir),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not executable" in response.json()["detail"].lower()
