import pytest
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from app.utils.disk_validator import disk_space_validator


@pytest.mark.unit
class TestDiskValidator:
    def test_validate_disk_space_success(self, tmp_path):
        """Test successful validation when space is sufficient."""
        f = tmp_path / "source.txt"
        f.write_text("some data")
        dest = tmp_path / "destination"
        dest.mkdir()
        
        # Should not raise
        disk_space_validator.validate_disk_space(f, dest)

    def test_validate_disk_space_direct_success(self, tmp_path):
        """Test direct validation with file size."""
        dest = tmp_path / "destination_direct"
        dest.mkdir()
        
        # Should not raise
        disk_space_validator.validate_disk_space_direct(1024, dest)

    def test_validate_disk_space_insufficient(self, tmp_path, monkeypatch):
        """Test validation failure when space is insufficient."""
        f = tmp_path / "large_source.txt"
        f.write_text("data")
        dest = tmp_path / "full_dest"
        dest.mkdir()
        
        # Mock disk usage to return only 1 byte free
        # Required space will be at least 1MB (default buffer)
        monkeypatch.setattr(shutil, "disk_usage", lambda p: (10**9, 10**9 - 1, 1))
        
        with pytest.raises(ValueError, match="Insufficient disk space"):
            disk_space_validator.validate_disk_space(f, dest)

    def test_validate_source_missing(self, tmp_path):
        """Test error when source file is missing."""
        with pytest.raises(ValueError, match="Source file does not exist"):
            disk_space_validator.validate_disk_space(tmp_path / "nonexistent", tmp_path)

    def test_validate_dest_missing(self, tmp_path):
        """Test error when destination directory is missing."""
        f = tmp_path / "src.txt"
        f.touch()
        with pytest.raises(ValueError, match="Destination directory does not exist"):
            disk_space_validator.validate_disk_space(f, tmp_path / "missing_dir")
