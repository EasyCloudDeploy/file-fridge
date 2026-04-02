import pytest
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    OperationType,
    StorageType,
)
from app.services.scan_progress import scan_progress_manager


@pytest.fixture(autouse=True)
def reset_scan_progress_manager():
    """Reset in-memory scan progress state between stats router tests."""
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()
    yield
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()


def _seed_live_freeze_state(db_session, monitored_path_factory, storage_location, tmp_path):
    """Create a real sparse 1GB file and partial freeze progress state."""
    hot_path = tmp_path / "hot_stats_live"
    cold_path = tmp_path / "cold_stats_live"
    hot_path.mkdir()
    cold_path.mkdir()

    storage_location.path = str(cold_path)
    db_session.commit()
    db_session.refresh(storage_location)

    monitored_path = monitored_path_factory("Stats Live Path", str(hot_path))
    monitored_path.operation_type = "move"
    db_session.commit()
    db_session.refresh(monitored_path)

    total_bytes = 1024 * 1024 * 1024
    source_file = hot_path / "stats-live-freeze.bin"
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
        "path_name": monitored_path.name,
        "storage_location_id": storage_location.id,
        "inventory_id": inventory.id,
        "source_file": source_file,
        "destination_file": destination_file,
        "operation_id": operation_id,
        "total_bytes": total_bytes,
    }


@pytest.mark.unit
class TestStatsRouter:
    def test_get_statistics_success(self, authenticated_client, db_session, monitored_path_factory):
        """Test getting overall statistics."""
        path = monitored_path_factory("Stat Path", "/tmp/hot_stats")
        # Add a record
        record = FileRecord(
            path_id=path.id,
            original_path="/tmp/hot_stats/f1.txt",
            cold_storage_path="/tmp/cold_stats/f1.txt",
            file_size=1024,
            operation_type=OperationType.MOVE,
            moved_at=datetime.now(timezone.utc),
        )
        db_session.add(record)
        db_session.commit()

        response = authenticated_client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_files_moved"] >= 1
        assert data["total_size_moved"] >= 1024
        assert "Stat Path" in data["files_by_path"]

    def test_get_statistics_live_metrics(
        self,
        authenticated_client,
        db_session,
        monitored_path_factory,
        storage_location,
        tmp_path,
    ):
        """Test /stats reflects real inventory and moved-file data during and after a freeze."""
        state = _seed_live_freeze_state(
            db_session,
            monitored_path_factory,
            storage_location,
            tmp_path,
        )

        try:
            response = authenticated_client.get("/api/v1/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["total_files_moved"] == 0
            assert data["total_size_moved"] == 0
            assert data["total_files_hot"] == 1
            assert data["total_files_cold"] == 0
            assert data["files_by_path"][state["path_name"]]["count"] == 0
            assert data["files_by_path"][state["path_name"]]["size"] == 0

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
            db_session.add(
                FileRecord(
                    path_id=state["path_id"],
                    original_path=str(state["source_file"]),
                    cold_storage_path=str(state["destination_file"]),
                    file_size=state["total_bytes"],
                    operation_type=OperationType.MOVE,
                    moved_at=datetime.now(timezone.utc),
                )
            )
            db_session.commit()

            response = authenticated_client.get("/api/v1/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["total_files_moved"] == 1
            assert data["total_size_moved"] == state["total_bytes"]
            assert data["total_files_hot"] == 0
            assert data["total_files_cold"] == 1
            assert data["files_by_path"][state["path_name"]]["count"] == 1
            assert data["files_by_path"][state["path_name"]]["size"] == state["total_bytes"]
        finally:
            scan_progress_manager.complete_file_operation(
                state["path_id"],
                state["operation_id"],
                "move_to_cold",
                success=False,
                error="test cleanup",
            )
            scan_progress_manager.finish_scan(state["path_id"], status="stopped")

    def test_get_detailed_statistics_success(
        self, authenticated_client, db_session, monitored_path_factory
    ):
        """Test getting detailed statistics."""
        path = monitored_path_factory("Detailed Path", "/tmp/hot_detailed")
        record = FileRecord(
            path_id=path.id,
            original_path="/tmp/hot_detailed/f1.txt",
            cold_storage_path="/tmp/cold_detailed/f1.txt",
            file_size=500,
            operation_type=OperationType.MOVE,
            moved_at=datetime.now(timezone.utc),
        )
        db_session.add(record)
        db_session.commit()

        response = authenticated_client.get("/api/v1/stats/detailed")
        assert response.status_code == 200
        data = response.json()
        assert data["total_files_moved"] >= 1
        assert "top_paths_by_files" in data
        assert "daily_activity" in data

    def test_get_aggregated_stats_success(self, authenticated_client):
        """Test getting aggregated statistics for different periods."""
        for period in ["daily", "weekly", "monthly"]:
            response = authenticated_client.get(f"/api/v1/stats/aggregated?period={period}&days=30")
            assert response.status_code == 200
            data = response.json()
            assert data["period"] == period
            assert "data" in data

    def test_cleanup_stats(self, authenticated_client, monkeypatch):
        """Test triggering stats cleanup."""
        # Mock the service
        from app.services.stats_cleanup import stats_cleanup_service

        monkeypatch.setattr(stats_cleanup_service, "cleanup_old_records", lambda db: {"deleted": 5})

        response = authenticated_client.post("/api/v1/stats/cleanup")
        assert response.status_code == 200
        assert response.json()["deleted"] == 5

    @pytest.mark.parametrize("seed", [11, 31, 71, 131, 313])
    def test_get_statistics_randomized_consistency(
        self,
        authenticated_client,
        db_session,
        monitored_path_factory,
        storage_location,
        tmp_path,
        seed,
    ):
        """Fuzz /stats with reproducible randomized inventory and file record state."""
        rng = random.Random(seed)
        expected_total_files_moved = 0
        expected_total_size_moved = 0
        expected_total_hot = 0
        expected_total_cold = 0
        expected_files_by_path = {}

        for index in range(rng.randint(2, 5)):
            hot_path = tmp_path / f"stats_seed_{seed}_{index}"
            monitored_path = monitored_path_factory(f"Stats Path {seed}-{index}", str(hot_path))
            db_session.commit()
            db_session.refresh(monitored_path)

            expected_files_by_path[monitored_path.name] = {"count": 0, "size": 0}

            for file_index in range(rng.randint(1, 5)):
                file_size = rng.randint(1, 64) * 1024
                storage_type = rng.choice([StorageType.HOT, StorageType.COLD])
                status = rng.choice(
                    [FileStatus.ACTIVE, FileStatus.MIGRATING, FileStatus.MOVED, FileStatus.MISSING]
                )

                if storage_type == StorageType.HOT:
                    file_path = hot_path / f"inventory_{file_index}.bin"
                    cold_storage_location_id = None
                else:
                    cold_dir = Path(storage_location.path)
                    file_path = cold_dir / f"inventory_{seed}_{index}_{file_index}.bin"
                    cold_storage_location_id = storage_location.id

                file_path.parent.mkdir(parents=True, exist_ok=True)
                with file_path.open("wb") as handle:
                    handle.truncate(file_size)

                db_session.add(
                    FileInventory(
                        path_id=monitored_path.id,
                        file_path=str(file_path),
                        file_size=file_size,
                        file_mtime=datetime.now(timezone.utc),
                        last_seen=datetime.now(timezone.utc),
                        status=status,
                        storage_type=storage_type,
                        cold_storage_location_id=cold_storage_location_id,
                    )
                )

                if status in [FileStatus.ACTIVE, FileStatus.MIGRATING, FileStatus.MOVED]:
                    if storage_type == StorageType.HOT:
                        expected_total_hot += 1
                    else:
                        expected_total_cold += 1

                if rng.choice([True, False]):
                    db_session.add(
                        FileRecord(
                            path_id=monitored_path.id,
                            original_path=str(hot_path / f"record_{file_index}.bin"),
                            cold_storage_path=str(
                                Path(storage_location.path)
                                / f"record_{seed}_{index}_{file_index}.bin"
                            ),
                            file_size=file_size,
                            operation_type=OperationType.MOVE,
                            moved_at=datetime.now(timezone.utc),
                        )
                    )
                    expected_total_files_moved += 1
                    expected_total_size_moved += file_size
                    expected_files_by_path[monitored_path.name]["count"] += 1
                    expected_files_by_path[monitored_path.name]["size"] += file_size

            db_session.commit()

        response = authenticated_client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()

        assert data["total_files_moved"] == expected_total_files_moved, f"seed={seed}"
        assert data["total_size_moved"] == expected_total_size_moved, f"seed={seed}"
        assert data["total_files_hot"] == expected_total_hot, f"seed={seed}"
        assert data["total_files_cold"] == expected_total_cold, f"seed={seed}"

        for path_name, expected in expected_files_by_path.items():
            assert data["files_by_path"][path_name]["count"] == expected["count"], f"seed={seed}"
            assert data["files_by_path"][path_name]["size"] == expected["size"], f"seed={seed}"
