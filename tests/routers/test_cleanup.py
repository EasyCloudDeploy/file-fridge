import pytest


@pytest.mark.unit
class TestCleanupRouter:
    def test_cleanup_missing_files_success(self, authenticated_client):
        """Test the endpoint for cleaning up missing files."""
        response = authenticated_client.post("/api/v1/cleanup")
        assert response.status_code == 200
        data = response.json()
        assert "removed" in data
        assert "errors" in data

    def test_cleanup_duplicates_success(self, authenticated_client):
        """Test the endpoint for cleaning up duplicate records."""
        response = authenticated_client.post("/api/v1/cleanup/duplicates")
        assert response.status_code == 200
        data = response.json()
        assert "removed" in data

    def test_cleanup_symlinks_success(self, authenticated_client):
        """Test the endpoint for cleaning up symlink inventory entries."""
        response = authenticated_client.post("/api/v1/cleanup/symlinks")
        assert response.status_code == 200
        data = response.json()
        assert "checked" in data
        assert "removed" in data
