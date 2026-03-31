import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy.orm import Session
from app.models import RelocationTask, FileInventory, MonitoredPath, RelocationTaskStatus
from app.database import SessionLocal
from app.main import app
from typing import Dict, Any

@pytest.fixture
def setup_migration_tasks(
    db_session: Session,
    monitored_path_factory: Any,
    file_inventory_factory: Any,
    storage_location: Any
) -> None:
    # Use SessionLocal to ensure it's written to the global DB (which RelocationTaskManager will use via SessionLocal())
    with SessionLocal() as db:
        # Clear existing ones from other tests
        db.query(RelocationTask).delete()
        db.commit()

        path = monitored_path_factory(name="mock_source", source_path="/tmp/mock_source")
        file1 = file_inventory_factory(path="/tmp/mock_source/test1.txt", size=1024, path_name="mock_source")
        file2 = file_inventory_factory(path="/tmp/mock_source/test2.txt", size=2048, path_name="mock_source")

        task1 = RelocationTask(
            task_id=str(uuid.uuid4()),
            inventory_id=file1.id,
            file_path="/tmp/mock_source/test1.txt",
            source_location_id=storage_location.id,
            source_location_name="mock_source",
            target_location_id=storage_location.id,
            target_location_name="mock_target",
            bytes_transferred=512,
            bytes_total=1024,
            status=RelocationTaskStatus.RUNNING
        )
        task2 = RelocationTask(
            task_id=str(uuid.uuid4()),
            inventory_id=file2.id,
            file_path="/tmp/mock_source/test2.txt",
            source_location_id=storage_location.id,
            source_location_name="mock_source",
            target_location_id=storage_location.id,
            target_location_name="mock_target",
            bytes_transferred=2048,
            bytes_total=2048,
            status=RelocationTaskStatus.COMPLETED
        )
        db.add(task1)
        db.add(task2)
        db.commit()

def test_get_active_migrations(
    authenticated_client: Any,
    setup_migration_tasks: None,
) -> None:
    """Test retrieving active migrations."""
    response = authenticated_client.get("/api/v1/migrations/active")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 1

    task = data[0]
    assert task["status"] == "running"
    assert task["file_path"] == "/tmp/mock_source/test1.txt"
    assert task["bytes_transferred"] == 512

def test_get_recent_migrations(
    authenticated_client: Any,
    setup_migration_tasks: None,
) -> None:
    """Test retrieving recent migrations."""
    response = authenticated_client.get("/api/v1/migrations/recent?limit=10")
    assert response.status_code == 200
    data = response.json()

    # Could be RUNNING or COMPLETED in recent (recent shows all)
    assert len(data) == 2
    statuses = [t["status"] for t in data]
    assert "running" in statuses
    assert "completed" in statuses

def test_get_migrations_unauthorized(
    client: Any,
) -> None:
    """Test retrieving migrations without auth."""
    response = client.get("/api/v1/migrations/active")
    assert response.status_code == 401

    response = client.get("/api/v1/migrations/recent")
    assert response.status_code == 401
