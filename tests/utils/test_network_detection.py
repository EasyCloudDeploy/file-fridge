import pytest
import platform
from app.utils.network_detection import is_network_mount, check_atime_availability


@pytest.mark.unit
class TestNetworkDetection:
    def test_is_network_mount_linux(self, monkeypatch):
        """Test network mount detection on Linux (currently always False)."""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        assert is_network_mount("/tmp") is False
        assert is_network_mount("/mnt/remote") is False

    def test_is_network_mount_macos_volumes(self, monkeypatch):
        """Test network mount detection on macOS under /Volumes."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        
        # Generic volume should be detected as network mount
        assert is_network_mount("/Volumes/NetworkDrive/file") is True
        
        # Specific local volumes should NOT be detected as network mount
        assert is_network_mount("/Volumes/Macintosh HD") is False
        assert is_network_mount("/Volumes/Macintosh HD - Data") is False
        assert is_network_mount("/Volumes/System") is False

    def test_is_network_mount_macos_other(self, monkeypatch):
        """Test network mount detection on macOS outside /Volumes."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        assert is_network_mount("/Users/test") is False

    def test_check_atime_availability_linux(self, monkeypatch):
        """Test atime availability on Linux (always True)."""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        available, msg = check_atime_availability("/any/path")
        assert available is True
        assert msg is None

    def test_check_atime_availability_macos_local(self, monkeypatch):
        """Test atime availability on macOS local path."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        available, msg = check_atime_availability("/Users/test")
        assert available is True
        assert msg is None

    def test_check_atime_availability_macos_network(self, monkeypatch):
        """Test atime availability on macOS network mount (False by default)."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        
        # Default: not allowed
        from app.config import settings
        monkeypatch.setattr(settings, "allow_atime_over_network_mounts", False)
        
        available, msg = check_atime_availability("/Volumes/RemoteData")
        assert available is False
        assert "unreliable" in msg.lower()

    def test_check_atime_availability_macos_network_override(self, monkeypatch):
        """Test atime availability on macOS network mount with override setting."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        
        from app.config import settings
        monkeypatch.setattr(settings, "allow_atime_over_network_mounts", True)
        
        available, msg = check_atime_availability("/Volumes/RemoteData")
        assert available is True
        assert msg is None

    def test_is_network_mount_exception_handling(self, monkeypatch):
        """Test exception handling in is_network_mount."""
        from pathlib import Path
        def mock_resolve(self):
            raise RuntimeError("Filesystem error")
        monkeypatch.setattr(Path, "resolve", mock_resolve)
        
        assert is_network_mount("/some/path") is False
