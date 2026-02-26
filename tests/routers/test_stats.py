import pytest
from datetime import datetime, timezone

from app.models import FileRecord, FileInventory, StorageType, OperationType, MonitoredPath


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
            moved_at=datetime.now(timezone.utc)
        )
        db_session.add(record)
        db_session.commit()
        
        response = authenticated_client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_files_moved"] >= 1
        assert data["total_size_moved"] >= 1024
        assert "Stat Path" in data["files_by_path"]

    def test_get_detailed_statistics_success(self, authenticated_client, db_session, monitored_path_factory):
        """Test getting detailed statistics."""
        path = monitored_path_factory("Detailed Path", "/tmp/hot_detailed")
        record = FileRecord(
            path_id=path.id,
            original_path="/tmp/hot_detailed/f1.txt",
            cold_storage_path="/tmp/cold_detailed/f1.txt",
            file_size=500,
            operation_type=OperationType.MOVE,
            moved_at=datetime.now(timezone.utc)
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
