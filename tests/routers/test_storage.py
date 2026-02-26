import pytest
from pathlib import Path

from app.models import ColdStorageLocation


@pytest.mark.unit
class TestStorageRouter:
    def test_list_storage_locations(self, authenticated_client, storage_location):
        """Test listing all storage locations."""
        response = authenticated_client.get("/api/v1/storage/locations")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        # Fixture might already have created one
        names = [loc["name"] for loc in data]
        assert storage_location.name in names

    def test_create_storage_location_success(self, authenticated_client, tmp_path):
        """Test successful creation of a storage location."""
        new_path = tmp_path / "new_storage_api"
        payload = {
            "name": "New API Storage",
            "path": str(new_path)
        }
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 201
        assert response.json()["name"] == "New API Storage"
        assert new_path.exists()
        assert new_path.is_dir()

    def test_create_storage_location_duplicate_name(self, authenticated_client, storage_location):
        """Test creating a storage location with a duplicate name."""
        payload = {
            "name": storage_location.name,
            "path": "/tmp/different_path",
        }
        response = authenticated_client.post("/api/v1/storage/locations", json=payload)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"].lower()

    def test_get_storage_stats(self, authenticated_client, storage_location):
        """Test getting storage statistics."""
        # Ensure path exists
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        
        response = authenticated_client.get("/api/v1/storage/stats")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        
        paths = [s["path"] for s in data]
        assert storage_location.path in paths
        assert "total_bytes" in data[0]

    def test_get_storage_location_not_found(self, authenticated_client):
        """Test getting a non-existent storage location."""
        response = authenticated_client.get("/api/v1/storage/locations/9999")
        assert response.status_code == 404

    def test_update_storage_location_success(self, authenticated_client, storage_location):
        """Test updating a storage location."""
        payload = {"name": "Newly Updated Name"}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}", 
            json=payload
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Newly Updated Name"

    def test_delete_storage_location_success(self, authenticated_client, db_session, tmp_path):
        """Test deleting an unused storage location."""
        loc_path = tmp_path / "to_delete_api"
        loc_path.mkdir()
        loc = ColdStorageLocation(name="Delete API", path=str(loc_path))
        db_session.add(loc)
        db_session.commit()
        
        response = authenticated_client.delete(f"/api/v1/storage/locations/{loc.id}")
        assert response.status_code == 200
        assert db_session.get(ColdStorageLocation, loc.id) is None

    def test_delete_storage_location_in_use(self, authenticated_client, db_session, monitored_path_factory, storage_location):
        """Test deleting a storage location that is in use (should fail without force)."""
        # monitored_path_factory uses the storage_location fixture
        monitored_path_factory("In Use Path", "/tmp/hot_in_use")
        
        response = authenticated_client.delete(f"/api/v1/storage/locations/{storage_location.id}")
        assert response.status_code == 400
        assert "still associated" in response.json()["detail"].lower()

    def test_force_delete_storage_location(self, authenticated_client, db_session, tmp_path, storage_location):
        """Test force deleting a storage location."""
        # Setup files in location
        loc_path = Path(storage_location.path)
        loc_path.mkdir(parents=True, exist_ok=True)
        test_file = loc_path / "to_be_purged.txt"
        test_file.write_text("goodbye world")
        
        response = authenticated_client.delete(f"/api/v1/storage/locations/{storage_location.id}?force=true")
        assert response.status_code == 200
        assert not test_file.exists()
        assert not loc_path.exists()
        assert db_session.get(ColdStorageLocation, storage_location.id) is None

    def test_toggle_encryption_on(self, authenticated_client, storage_location, monkeypatch):
        """Test enabling encryption for a storage location."""
        # Mock scheduler to avoid actual background job triggering errors
        from app.services.scheduler import scheduler_service
        monkeypatch.setattr(scheduler_service, "trigger_encryption_job", lambda x: None)
        
        payload = {"is_encrypted": True}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}", 
            json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_encrypted"] is True
        assert data["encryption_status"] == "pending"

    def test_toggle_encryption_off(self, authenticated_client, db_session, storage_location, monkeypatch):
        """Test disabling encryption for a storage location."""
        # Mock scheduler
        from app.services.scheduler import scheduler_service
        monkeypatch.setattr(scheduler_service, "trigger_decryption_job", lambda x: None)
        
        # Manually set to encrypted state first
        loc = db_session.get(ColdStorageLocation, storage_location.id)
        loc.is_encrypted = True
        loc.encryption_status = "encrypted"
        db_session.commit()
        
        payload = {"is_encrypted": False}
        response = authenticated_client.put(
            f"/api/v1/storage/locations/{storage_location.id}", 
            json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_encrypted"] is False
        assert data["encryption_status"] == "decrypting"
