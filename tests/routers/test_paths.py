from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import MonitoredPath, ColdStorageLocation, ScanStatus


def test_list_paths(authenticated_client: TestClient, monitored_path_factory):
    """Test listing all monitored paths."""
    monitored_path_factory("Path 1", "/tmp/hot1")
    monitored_path_factory("Path 2", "/tmp/hot2")

    response = authenticated_client.get("/api/v1/paths")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Path 1"


@patch("app.services.scheduler.scheduler_service.add_path_job")
def test_create_path(mock_add_job, authenticated_client: TestClient, storage_location: ColdStorageLocation, tmp_path):
    """Test creating a new monitored path."""
    source_path = tmp_path / "new_hot"
    source_path.mkdir()
    
    path_data = {
        "name": "New Path",
        "source_path": str(source_path),
        "operation_type": "move",
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }

    response = authenticated_client.post("/api/v1/paths", json=path_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Path"
    assert data["source_path"] == str(source_path)
    mock_add_job.assert_called_once()

def test_create_path_invalid_source(authenticated_client: TestClient, storage_location: ColdStorageLocation):
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
def test_delete_path(mock_remove_job, authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test deleting a monitored path."""
    path = monitored_path_factory("To Be Deleted", str(tmp_path / "delete_me"))
    
    response = authenticated_client.delete(f"/api/v1/paths/{path.id}")
    assert response.status_code == 200
    
    # Verify it's gone
    response = authenticated_client.get(f"/api/v1/paths/{path.id}")
    assert response.status_code == 404
    mock_remove_job.assert_called_once_with(path.id)

@patch("app.services.scheduler.scheduler_service.trigger_scan")
def test_trigger_scan(mock_trigger_scan, authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test triggering a scan for a path."""
    path = monitored_path_factory("Scan Me", str(tmp_path / "scan_hot"))
    
    response = authenticated_client.post(f"/api/v1/paths/{path.id}/scan")
    
    assert response.status_code == 202
    assert "Scan triggered" in response.json()["message"]
    mock_trigger_scan.assert_called_once_with(path.id)

@patch("app.services.scan_progress.scan_progress_manager.get_progress")
def test_get_scan_progress(mock_get_progress, authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test getting the scan progress for a path."""
    path = monitored_path_factory("Progress Path", str(tmp_path / "progress_hot"))
    mock_progress_data = {
        "status": "running", "progress": {"percent": 50}, "current_operations": []
    }
    mock_get_progress.return_value = mock_progress_data

    response = authenticated_client.get(f"/api/v1/paths/{path.id}/scan/progress")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["progress"]["percent"] == 50
    mock_get_progress.assert_called_once_with(path.id)

def test_get_scan_errors(authenticated_client: TestClient, db_session: Session, monitored_path_factory, tmp_path):
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
def test_get_hot_storage_stats(mock_disk_usage, authenticated_client: TestClient, monitored_path_factory, tmp_path):
    """Test the /stats endpoint."""
    path = monitored_path_factory("Stats Path", str(tmp_path / "stats_hot"))
    mock_disk_usage.return_value = (1000, 500, 500) # total, used, free

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

def test_create_path_duplicate_name(authenticated_client: TestClient, monitored_path_factory, storage_location, tmp_path):
    """Test creating a path with a duplicate name."""
    monitored_path_factory("Existing Path", str(tmp_path / "hot1"))
    
    payload = {
        "name": "Existing Path",
        "source_path": str(tmp_path / "hot2"),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id]
    }
    # Create hot2
    Path(payload["source_path"]).mkdir()
    
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()

def test_delete_path_undo_operations(authenticated_client: TestClient, db_session, monitored_path_factory, tmp_path, monkeypatch):
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
        "storage_location_ids": [storage_location.id]
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"].lower()

def test_update_path_success(authenticated_client: TestClient, monitored_path_factory, tmp_path, storage_location):
    """Test updating multiple fields of a monitored path."""
    path = monitored_path_factory("Original Name", str(tmp_path / "orig_hot"))
    new_hot = tmp_path / "new_hot_upd"
    new_hot.mkdir()
    
    payload = {
        "name": "Updated Path Name",
        "source_path": str(new_hot),
        "check_interval_seconds": 7200,
        "enabled": False
    }
    response = authenticated_client.put(f"/api/v1/paths/{path.id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Path Name"
    assert data["check_interval_seconds"] == 7200
    assert data["enabled"] is False

def test_update_path_storage_locations(authenticated_client: TestClient, monitored_path_factory, db_session, tmp_path, storage_location):
    """Test updating storage locations for a path."""
    path = monitored_path_factory("Multi Loc Path", str(tmp_path / "multi_hot"))
    
    # Create another storage location
    new_loc = ColdStorageLocation(name="Secondary Storage", path=str(tmp_path / "secondary"))
    Path(new_loc.path).mkdir()
    db_session.add(new_loc)
    db_session.commit()
    
    payload = {
        "storage_location_ids": [storage_location.id, new_loc.id]
    }
    response = authenticated_client.put(f"/api/v1/paths/{path.id}", json=payload)
    assert response.status_code == 200
    # Check if both are associated
    assert len(response.json()["storage_locations"]) == 2

def test_validate_path_access_not_writable(authenticated_client: TestClient, storage_location, tmp_path, monkeypatch):
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
        "storage_location_ids": [storage_location.id]
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not writable" in response.json()["detail"].lower()

def test_validate_path_access_not_readable(authenticated_client: TestClient, storage_location, tmp_path, monkeypatch):
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
        "storage_location_ids": [storage_location.id]
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not readable" in response.json()["detail"].lower()

def test_validate_path_access_not_executable(authenticated_client: TestClient, storage_location, tmp_path, monkeypatch):
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
        "storage_location_ids": [storage_location.id]
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not executable" in response.json()["detail"].lower()
