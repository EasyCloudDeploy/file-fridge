import pytest
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.models import ColdStorageLocation, MonitoredPath, StorageType
from app.services.storage_routing_service import storage_routing_service


@pytest.mark.unit
class TestStorageRoutingService:
    def test_select_storage_location_success(self, db_session, monitored_path_factory, storage_location):
        """Test basic selection of storage location."""
        path = monitored_path_factory("Route Success", "/tmp/hot_route")
        # Ensure storage_location path exists and has space
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        
        # Mock disk usage to ensure enough space
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            loc = storage_routing_service.select_storage_location(db_session, path, 1024)
            assert loc is not None
            assert loc.id == storage_location.id

    def test_select_storage_location_multiple_scoring(self, db_session, monitored_path_factory, tmp_path):
        """Test selection between multiple locations based on scoring."""
        loc1_path = tmp_path / "loc1"
        loc1_path.mkdir()
        loc1 = ColdStorageLocation(name="Loc 1", path=str(loc1_path))
        
        loc2_path = tmp_path / "loc2"
        loc2_path.mkdir()
        loc2 = ColdStorageLocation(name="Loc 2", path=str(loc2_path))
        
        db_session.add_all([loc1, loc2])
        db_session.commit()
        
        path = monitored_path_factory("Multi Route", "/tmp/hot_multi")
        path.storage_locations = [loc1, loc2]
        db_session.commit()
        
        # Mock disk usage to favor loc2 (more free space)
        def mock_usage(p):
            if str(p) == str(loc1_path):
                # 2GB free (just above minimum 1GB)
                return MagicMock(total=100*10**9, used=98*10**9, free=2*10**9)
            # 50GB free
            return MagicMock(total=100*10**9, used=50*10**9, free=50*10**9)
            
        with patch("shutil.disk_usage", side_effect=mock_usage):
            selected = storage_routing_service.select_storage_location(db_session, path, 1024)
            assert selected is not None
            assert selected.id == loc2.id

    def test_select_storage_location_insufficient_space(self, db_session, monitored_path_factory, storage_location):
        """Test failure when no location has enough space."""
        path = monitored_path_factory("No Space Path", "/tmp/hot_nospace")
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        
        # Mock disk usage to return very low space
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**9, used=10**9-1, free=1)):
            loc = storage_routing_service.select_storage_location(db_session, path, 1024)
            assert loc is None

    def test_has_sufficient_space(self, storage_location):
        """Test the has_sufficient_space helper."""
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            assert storage_routing_service.has_sufficient_space(storage_location, 1024) is True
            
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**9, used=10**9-1, free=1)):
            assert storage_routing_service.has_sufficient_space(storage_location, 1024) is False

    def test_get_location_health(self, db_session, storage_location):
        """Test retrieving location health metrics."""
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            health = storage_routing_service.get_location_health(db_session, storage_location, 1)
            assert health["healthy"] is True
            assert health["free_space_bytes"] == 10**11

    def test_get_location_health_not_accessible(self, db_session, storage_location):
        """Test health metrics for non-accessible location."""
        storage_location.path = "/non/existent/storage/path"
        
        health = storage_routing_service.get_location_health(db_session, storage_location, 1)
        assert health["healthy"] is False
        assert "not accessible" in health["reason"].lower()
