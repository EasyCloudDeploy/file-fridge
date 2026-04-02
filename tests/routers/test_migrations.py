import uuid
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import FileInventory, FileStatus, RelocationTask, RelocationTaskStatus, StorageType
from app.services.scan_progress import scan_progress_manager


@pytest.fixture(autouse=True)
def reset_scan_progress_manager() -> None:
    """Reset in-memory scan progress state between migrations tests."""
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()

    yield

    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()


@pytest.fixture
def setup_migration_tasks(
    db_session: Session,
    monitored_path_factory: Any,
    file_inventory_factory: Any,
    storage_location: Any,
) -> None:
    # Use db_session since it is the one bound to the router's get_db dependency via testing overrides!
    db_session.query(RelocationTask).delete()
    db_session.commit()

    path = monitored_path_factory(name="mock_source", source_path="/tmp/mock_source")  # NOSONAR
    file1 = file_inventory_factory(
        path="/tmp/mock_source/test1.txt", size=1024, path_name="mock_source"
    )  # NOSONAR
    file2 = file_inventory_factory(
        path="/tmp/mock_source/test2.txt", size=2048, path_name="mock_source"
    )  # NOSONAR

    task1 = RelocationTask(
        task_id=str(uuid.uuid4()),
        inventory_id=file1.id,
        file_path="/tmp/mock_source/test1.txt",  # NOSONAR
        source_location_id=storage_location.id,
        source_location_name="mock_source",
        target_location_id=storage_location.id,
        target_location_name="mock_target",
        bytes_transferred=512,
        bytes_total=1024,
        status=RelocationTaskStatus.RUNNING,
    )
    task2 = RelocationTask(
        task_id=str(uuid.uuid4()),
        inventory_id=file2.id,
        file_path="/tmp/mock_source/test2.txt",  # NOSONAR
        source_location_id=storage_location.id,
        source_location_name="mock_source",
        target_location_id=storage_location.id,
        target_location_name="mock_target",
        bytes_transferred=2048,
        bytes_total=2048,
        status=RelocationTaskStatus.COMPLETED,
    )
    db_session.add(task1)
    db_session.add(task2)
    db_session.commit()


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
    assert task["file_path"] == "/tmp/mock_source/test1.txt"  # NOSONAR
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


def _seed_live_freeze_state(
    db_session: Session,
    monitored_path_factory: Any,
    storage_location: Any,
    tmp_path: Path,
) -> dict[str, Any]:
    """Create a real sparse 1GB file and partial freeze progress state."""
    hot_path = tmp_path / "hot"
    cold_path = tmp_path / "cold"
    hot_path.mkdir()
    cold_path.mkdir()

    storage_location.path = str(cold_path)
    db_session.commit()
    db_session.refresh(storage_location)

    monitored_path = monitored_path_factory("progress-path", str(hot_path))
    monitored_path.operation_type = "move"
    db_session.commit()
    db_session.refresh(monitored_path)

    total_bytes = 1024 * 1024 * 1024
    source_file = hot_path / "large-file.bin"
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

    pause_mb = random.Random(20260402).choice([16, 24, 32, 48, 64])
    pause_bytes = pause_mb * 1024 * 1024
    destination_file = cold_path / source_file.name
    scan_progress_manager.start_scan(monitored_path.id, total_files=1)
    operation_id = scan_progress_manager.start_file_operation(
        monitored_path.id,
        source_file.name,
        "move_to_cold",
        total_bytes,
        file_path=str(source_file),
        destination_path=str(destination_file),
    )
    inventory.status = FileStatus.MIGRATING
    db_session.commit()

    from app.services.file_mover import FileMover
    from app.models import OperationType

    def progress_callback(transferred: int) -> None:
        scan_progress_manager.update_file_progress(
            monitored_path.id, operation_id, transferred
        )
        if transferred >= pause_bytes:
            raise RuntimeError("Paused for test")

    # Ensure cross-filesystem move behavior by temporarily unlinking the actual file
    # to force OSError on rename inside _move if necessary, or just force the copy code path.
    # We will use monkeypatching to ensure `rename` fails and it uses `_copy_with_progress`.
    original_rename = Path.rename
    def mock_rename(self, target):
        raise OSError("Cross-device link")
    Path.rename = mock_rename

    try:
        FileMover.move_with_rollback(
            source_file,
            destination_file,
            OperationType.MOVE,
            verify_checksum=False,
            progress_callback=progress_callback
        )
    except RuntimeError as e:
        if str(e) != "Paused for test":
            raise
    finally:
        Path.rename = original_rename

    time.sleep(0.05)

    return {
        "monitored_path_id": monitored_path.id,
        "inventory_id": inventory.id,
        "storage_location_name": storage_location.name,
        "source_file": source_file,
        "destination_file": destination_file,
        "operation_id": operation_id,
        "total_bytes": total_bytes,
        "pause_bytes": pause_bytes,
    }


def _seed_live_thaw_state(
    db_session: Session,
    monitored_path_factory: Any,
    storage_location: Any,
    tmp_path: Path,
) -> dict[str, Any]:
    """Create a real sparse 1GB file and partial thaw progress state."""
    hot_path = tmp_path / "hot"
    cold_path = tmp_path / "cold"
    hot_path.mkdir()
    cold_path.mkdir()

    storage_location.path = str(cold_path)
    db_session.commit()
    db_session.refresh(storage_location)

    monitored_path = monitored_path_factory("progress-path-thaw", str(hot_path))
    monitored_path.operation_type = "move"
    db_session.commit()
    db_session.refresh(monitored_path)

    total_bytes = 1024 * 1024 * 1024
    cold_file = cold_path / "large-file.bin"
    with cold_file.open("wb") as handle:
        handle.truncate(total_bytes)

    inventory = FileInventory(
        path_id=monitored_path.id,
        file_path=str(cold_file),
        file_size=total_bytes,
        file_mtime=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        status=FileStatus.MIGRATING,
        storage_type=StorageType.COLD,
        cold_storage_location_id=storage_location.id,
    )
    db_session.add(inventory)
    db_session.commit()
    db_session.refresh(inventory)

    pause_mb = random.Random(20260403).choice([16, 24, 32, 48, 64])
    pause_bytes = pause_mb * 1024 * 1024
    destination_file = hot_path / cold_file.name
    scan_progress_manager.start_scan(monitored_path.id, total_files=1)
    operation_id = scan_progress_manager.start_file_operation(
        monitored_path.id,
        cold_file.name,
        "move_to_hot",
        total_bytes,
        file_path=str(cold_file),
        destination_path=str(destination_file),
    )

    from app.services.file_thawer import FileThawer

    def progress_callback(transferred: int) -> None:
        scan_progress_manager.update_file_progress(
            monitored_path.id, operation_id, transferred
        )
        if transferred >= pause_bytes:
            raise RuntimeError("Paused for test")

    original_rename = Path.rename
    def mock_rename(self, target):
        raise OSError("Cross-device link")
    Path.rename = mock_rename

    try:
        FileThawer._move_preserving_timestamps(
            cold_file,
            destination_file,
            progress_callback=progress_callback
        )
    except RuntimeError as e:
        if str(e) != "Paused for test":
            raise
    finally:
        Path.rename = original_rename

    time.sleep(0.05)

    return {
        "monitored_path_id": monitored_path.id,
        "inventory_id": inventory.id,
        "storage_location_name": storage_location.name,
        "source_file": cold_file,
        "destination_file": destination_file,
        "operation_id": operation_id,
        "total_bytes": total_bytes,
        "pause_bytes": pause_bytes,
    }


def test_get_freezing_files_returns_live_progress_metrics(
    authenticated_client: Any,
    db_session: Session,
    monitored_path_factory: Any,
    storage_location: Any,
    tmp_path: Path,
) -> None:
    """Test /freezing returns live progress metrics while a real freeze is in flight."""
    state = _seed_live_freeze_state(
        db_session=db_session,
        monitored_path_factory=monitored_path_factory,
        storage_location=storage_location,
        tmp_path=tmp_path,
    )

    try:
        db_session.expire_all()

        response = authenticated_client.get("/api/v1/migrations/freezing")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        freezing_file = data[0]
        assert freezing_file["inventory_id"] == state["inventory_id"]
        assert freezing_file["file_path"] == str(state["source_file"])
        assert freezing_file["destination_path"] == str(state["destination_file"])
        assert freezing_file["operation_type"] == "freeze"
        assert freezing_file["source_label"] == "Hot Storage"
        assert freezing_file["target_label"] == "Cold Storage"
        assert freezing_file["file_size"] == state["total_bytes"]
        assert freezing_file["transferred_bytes"] == state["pause_bytes"]
        assert freezing_file["total_bytes"] == state["total_bytes"]
        assert 0 < freezing_file["percent_complete"] < 100
        assert freezing_file["percent_complete"] == int(
            (state["pause_bytes"] / state["total_bytes"]) * 100
        )
        assert freezing_file["transfer_rate_bytes_per_sec"] > 0
        assert freezing_file["eta_seconds"] is not None
        assert freezing_file["eta_seconds"] > 0
    finally:
        scan_progress_manager.complete_file_operation(
            state["monitored_path_id"],
            state["operation_id"],
            "move_to_cold",
            success=False,
            error="test cleanup",
        )
        scan_progress_manager.finish_scan(state["monitored_path_id"], status="stopped")
        (
            db_session.query(FileInventory)
            .filter(FileInventory.id == state["inventory_id"])
            .update({"status": FileStatus.ACTIVE})
        )
        db_session.commit()

    db_session.expire_all()
    response = authenticated_client.get("/api/v1/migrations/freezing")
    assert response.status_code == 200
    assert response.json() == []


def test_get_freezing_files_returns_live_thaw_progress_metrics(
    authenticated_client: Any,
    db_session: Session,
    monitored_path_factory: Any,
    storage_location: Any,
    tmp_path: Path,
) -> None:
    """Test /freezing returns live progress metrics while a real thaw is in flight."""
    state = _seed_live_thaw_state(
        db_session=db_session,
        monitored_path_factory=monitored_path_factory,
        storage_location=storage_location,
        tmp_path=tmp_path,
    )

    try:
        db_session.expire_all()

        response = authenticated_client.get("/api/v1/migrations/freezing")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        freezing_file = data[0]
        assert freezing_file["inventory_id"] == state["inventory_id"]
        assert freezing_file["file_path"] == str(state["source_file"])
        assert freezing_file["destination_path"] == str(state["destination_file"])
        assert freezing_file["operation_type"] == "thaw"
        assert freezing_file["source_label"] == state["storage_location_name"]
        assert freezing_file["target_label"] == "Hot Storage"
        assert freezing_file["file_size"] == state["total_bytes"]
        assert freezing_file["transferred_bytes"] == state["pause_bytes"]
        assert freezing_file["total_bytes"] == state["total_bytes"]
        assert 0 < freezing_file["percent_complete"] < 100
        assert freezing_file["percent_complete"] == int(
            (state["pause_bytes"] / state["total_bytes"]) * 100
        )
        assert freezing_file["transfer_rate_bytes_per_sec"] > 0
        assert freezing_file["eta_seconds"] is not None
        assert freezing_file["eta_seconds"] > 0
    finally:
        scan_progress_manager.complete_file_operation(
            state["monitored_path_id"],
            state["operation_id"],
            "move_to_hot",
            success=False,
            error="test cleanup",
        )
        scan_progress_manager.finish_scan(state["monitored_path_id"], status="stopped")
        (
            db_session.query(FileInventory)
            .filter(FileInventory.id == state["inventory_id"])
            .update({"status": FileStatus.ACTIVE})
        )
        db_session.commit()

    db_session.expire_all()
    response = authenticated_client.get("/api/v1/migrations/freezing")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("seed", [7, 23, 89, 211, 997])
def test_get_freezing_files_randomized_metrics_consistency(
    authenticated_client: Any,
    db_session: Session,
    monitored_path_factory: Any,
    storage_location: Any,
    tmp_path: Path,
    seed: int,
) -> None:
    """Fuzz /freezing with reproducible randomized inventory and progress state."""
    rng = random.Random(seed)
    hot_path = tmp_path / f"hot_{seed}"
    cold_path = tmp_path / f"cold_{seed}"
    hot_path.mkdir()
    cold_path.mkdir()

    storage_location.path = str(cold_path)
    db_session.commit()
    db_session.refresh(storage_location)
    storage_location_id = storage_location.id
    storage_location_name = storage_location.name

    monitored_path = monitored_path_factory(f"fuzz-path-{seed}", str(hot_path))
    db_session.commit()
    db_session.refresh(monitored_path)

    scan_progress_manager.start_scan(monitored_path.id, total_files=10)
    expected_by_inventory_id = {}
    active_relocation_inventory_ids = set()

    try:
        for index in range(rng.randint(4, 8)):
            file_size = rng.randint(1, 256) * 1024 * 1024
            storage_type = rng.choice([StorageType.HOT, StorageType.COLD])
            status = rng.choice(
                [FileStatus.MIGRATING, FileStatus.ACTIVE, FileStatus.MOVED, FileStatus.MISSING]
            )

            if storage_type == StorageType.HOT:
                file_path = hot_path / f"file_{index}.bin"
                source_label = "Hot Storage"
                target_label = "Cold Storage"
                operation = "move_to_cold"
                operation_type = "freeze"
                cold_storage_location_id = None
            else:
                file_path = cold_path / f"file_{index}.bin"
                source_label = storage_location_name
                target_label = "Hot Storage"
                operation = "move_to_hot"
                operation_type = "thaw"
                cold_storage_location_id = storage_location_id

            with file_path.open("wb") as handle:
                handle.truncate(file_size)

            inventory = FileInventory(
                path_id=monitored_path.id,
                file_path=str(file_path),
                file_size=file_size,
                file_mtime=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                status=status,
                storage_type=storage_type,
                cold_storage_location_id=cold_storage_location_id,
            )
            db_session.add(inventory)
            db_session.commit()
            db_session.refresh(inventory)

            if status == FileStatus.MIGRATING and rng.choice([True, False]):
                task = RelocationTask(
                    task_id=str(uuid.uuid4()),
                    inventory_id=inventory.id,
                    file_path=inventory.file_path,
                    source_location_id=storage_location_id,
                    source_location_name="source",
                    target_location_id=storage_location_id,
                    target_location_name="target",
                    bytes_transferred=0,
                    bytes_total=file_size,
                    status=RelocationTaskStatus.RUNNING,
                )
                db_session.add(task)
                db_session.commit()
                active_relocation_inventory_ids.add(inventory.id)

            progress_bytes = rng.randint(0, file_size)
            progress_entry = rng.choice(
                ["matching", "mismatched_path", "mismatched_operation", "missing"]
            )
            if progress_entry != "missing":
                destination_path = str(
                    (cold_path / file_path.name)
                    if operation == "move_to_cold"
                    else (hot_path / file_path.name)
                )
                operation_id = scan_progress_manager.start_file_operation(
                    monitored_path.id,
                    file_path.name,
                    operation if progress_entry != "mismatched_operation" else "copy",
                    file_size,
                    file_path=(
                        inventory.file_path
                        if progress_entry == "matching"
                        else str(file_path.with_name(f"other_{index}.bin"))
                    ),
                    destination_path=destination_path,
                )
                time.sleep(0.01)
                scan_progress_manager.update_file_progress(
                    monitored_path.id, operation_id, progress_bytes
                )

            if (
                status == FileStatus.MIGRATING
                and inventory.id not in active_relocation_inventory_ids
            ):
                if progress_entry == "matching":
                    expected_destination_path = str(
                        (cold_path / file_path.name)
                        if operation == "move_to_cold"
                        else (hot_path / file_path.name)
                    )
                elif operation == "move_to_hot":
                    expected_destination_path = str(hot_path / file_path.name)
                else:
                    expected_destination_path = None

                expected_by_inventory_id[inventory.id] = {
                    "inventory_id": inventory.id,
                    "file_path": inventory.file_path,
                    "destination_path": expected_destination_path,
                    "operation_type": operation_type,
                    "source_label": source_label,
                    "target_label": target_label,
                    "file_size": file_size,
                    "transferred_bytes": progress_bytes if progress_entry == "matching" else 0,
                    "total_bytes": file_size,
                    "percent_complete": (
                        int((progress_bytes / file_size) * 100)
                        if progress_entry == "matching" and file_size
                        else 0
                    ),
                }

        response = authenticated_client.get("/api/v1/migrations/freezing")
        assert response.status_code == 200
        data = response.json()

        actual_by_inventory_id = {item["inventory_id"]: item for item in data}
        assert set(actual_by_inventory_id) == set(expected_by_inventory_id), f"seed={seed}"

        for inventory_id, expected in expected_by_inventory_id.items():
            actual = actual_by_inventory_id[inventory_id]
            assert actual["file_path"] == expected["file_path"], f"seed={seed}"
            assert actual["destination_path"] == expected["destination_path"], f"seed={seed}"
            assert actual["operation_type"] == expected["operation_type"], f"seed={seed}"
            assert actual["source_label"] == expected["source_label"], f"seed={seed}"
            assert actual["target_label"] == expected["target_label"], f"seed={seed}"
            assert actual["file_size"] == expected["file_size"], f"seed={seed}"
            assert actual["transferred_bytes"] == expected["transferred_bytes"], f"seed={seed}"
            assert actual["total_bytes"] == expected["total_bytes"], f"seed={seed}"
            assert actual["percent_complete"] == expected["percent_complete"], f"seed={seed}"
            assert 0 <= actual["percent_complete"] <= 100, f"seed={seed}"
            assert actual["transfer_rate_bytes_per_sec"] >= 0, f"seed={seed}"
    finally:
        scan_progress_manager.finish_scan(monitored_path.id, status="stopped")
