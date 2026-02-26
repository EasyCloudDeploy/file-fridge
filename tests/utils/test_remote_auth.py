import pytest
import time
from app.utils.remote_auth import RemoteAuth, remote_auth


@pytest.mark.unit
class TestRemoteAuth:
    def test_singleton(self):
        """Test that RemoteAuth follows the singleton pattern."""
        auth1 = RemoteAuth()
        auth2 = RemoteAuth()
        assert auth1 is auth2
        assert auth1 is remote_auth

    def test_get_code(self):
        """Test getting the current code."""
        code = remote_auth.get_code()
        assert isinstance(code, str)
        assert len(code) == 64  # SHA256 hex string length

    def test_rotate_code(self):
        """Test rotating the code."""
        old_code = remote_auth.get_code()
        remote_auth.rotate_code()
        new_code = remote_auth.get_code()
        assert old_code != new_code
        assert len(new_code) == 64

    def test_get_code_with_expiry(self):
        """Test getting code with its remaining lifetime."""
        code, expiry = remote_auth.get_code_with_expiry()
        assert isinstance(code, str)
        assert isinstance(expiry, int)
        # Should be between 0 and 1 hour (3600 seconds)
        assert 0 <= expiry <= 3600
