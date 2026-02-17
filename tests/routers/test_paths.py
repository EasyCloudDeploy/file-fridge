from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ColdStorageLocation, MonitoredPath


@pytest.fixture
def storage_location(db_session: Session):
    """Fixture for a ColdStorageLocation object."""
    location = ColdStorageLocation(
        name="Test Cold Storage",
        path="/tmp/cold_storage",
        is_default=True,
    )
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    # Create the directory
    Path(location.path).mkdir(exist_ok=True, parents=True)
    return location


@pytest.fixture
def monitored_path_factory(db_session: Session, storage_location: ColdStorageLocation):
    """Factory fixture to create MonitoredPath objects."""

    def _factory(name: str, source_path: str):
        path = MonitoredPath(
            name=name,
            source_path=source_path,
            storage_locations=[storage_location],
        )
        db_session.add(path)
        db_session.commit()
        db_session.refresh(path)
        # Create the directory
        Path(path.source_path).mkdir(exist_ok=True, parents=True)
        return path

    return _factory


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
        "check_interval": 3600,
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
    path.last_scan_status = "FAILURE"
    path.last_scan_error_log = "Disk full"
    db_session.commit()

    response = authenticated_client.get(f"/api/v1/paths/{path.id}/scan-errors")
    assert response.status_code == 200
    data = response.json()
    assert data["last_scan_status"] == "FAILURE"
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
