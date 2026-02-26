import pytest
from pathlib import Path

from app.utils.indexing import IndexingManager


@pytest.mark.unit
class TestIndexingManager:
    def test_create_noindex_file_success(self, tmp_path):
        """Test creating a .noindex file."""
        dir_path = tmp_path / "test_noindex"
        # Directory doesn't exist, should be created
        assert IndexingManager.create_noindex_file(str(dir_path)) is True
        assert dir_path.exists()
        assert (dir_path / ".noindex").exists()
        
        # Call again when it already exists
        assert IndexingManager.create_noindex_file(str(dir_path)) is True
        assert (dir_path / ".noindex").exists()

    def test_remove_noindex_file_success(self, tmp_path):
        """Test removing a .noindex file."""
        dir_path = tmp_path / "test_remove_noindex"
        dir_path.mkdir()
        noindex = dir_path / ".noindex"
        noindex.touch()
        
        assert IndexingManager.remove_noindex_file(str(dir_path)) is True
        assert not noindex.exists()
        
        # Call when it doesn't exist
        assert IndexingManager.remove_noindex_file(str(dir_path)) is True

    def test_remove_noindex_dir_not_exists(self, tmp_path):
        """Test removing .noindex from non-existent directory."""
        assert IndexingManager.remove_noindex_file("/non/existent/path") is True

    def test_manage_noindex_files_flow(self, tmp_path):
        """Test managing .noindex files for both directories."""
        hot = tmp_path / "hot_indexing"
        cold = tmp_path / "cold_indexing"
        
        # Enable indexing prevention
        assert IndexingManager.manage_noindex_files(str(hot), str(cold), True) is True
        assert (hot / ".noindex").exists()
        assert (cold / ".noindex").exists()
        
        # Disable indexing prevention
        assert IndexingManager.manage_noindex_files(str(hot), str(cold), False) is True
        assert not (hot / ".noindex").exists()
        assert not (cold / ".noindex").exists()

    def test_create_noindex_file_failure(self, monkeypatch):
        """Test handling failure during .noindex creation."""
        from pathlib import Path
        def mock_mkdir(self, **kwargs):
            raise PermissionError("Permission denied")
        monkeypatch.setattr(Path, "mkdir", mock_mkdir)
        
        assert IndexingManager.create_noindex_file("/root/path") is False

    def test_remove_noindex_file_failure(self, tmp_path, monkeypatch):
        """Test handling failure during .noindex removal."""
        dir_path = tmp_path / "fail_remove"
        dir_path.mkdir()
        noindex = dir_path / ".noindex"
        noindex.touch()
        
        def mock_unlink(self):
            raise PermissionError("Locked")
        monkeypatch.setattr(Path, "unlink", mock_unlink)
        
        assert IndexingManager.remove_noindex_file(str(dir_path)) is False
