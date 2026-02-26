import pytest
import time
import os
from unittest.mock import MagicMock

from app.utils.rate_limiter import RateLimiter, get_rate_limit_key, check_rate_limit


@pytest.mark.unit
class TestRateLimiter:
    def test_rate_limiter_basic_flow(self, monkeypatch):
        """Test basic rate limiting logic."""
        monkeypatch.setenv("TESTING", "false")
        limiter = RateLimiter(requests_per_minute=3)
        
        # 3 requests allowed
        assert limiter.is_allowed("user1") is True
        assert limiter.is_allowed("user1") is True
        assert limiter.is_allowed("user1") is True
        # 4th denied
        assert limiter.is_allowed("user1") is False
        
        # Different identifier still allowed
        assert limiter.is_allowed("user2") is True

    def test_rate_limiter_testing_bypass(self, monkeypatch):
        """Test that rate limiting is bypassed in testing mode."""
        monkeypatch.setenv("TESTING", "true")
        monkeypatch.setenv("DISABLE_RATE_LIMIT", "true")
        limiter = RateLimiter(requests_per_minute=1)
        
        assert limiter.is_allowed("u") is True
        assert limiter.is_allowed("u") is True
        assert limiter.is_allowed("u") is True

    def test_rate_limiter_expiration(self, monkeypatch):
        """Test that old requests expire."""
        monkeypatch.setenv("TESTING", "false")
        limiter = RateLimiter(requests_per_minute=1)
        
        now = time.time()
        # Manually inject an old request
        limiter.requests["u1"] = [now - 61]
        
        # Should be allowed now because old one expired
        assert limiter.is_allowed("u1") is True

    def test_cleanup_method(self, monkeypatch):
        """Test internal _cleanup method."""
        limiter = RateLimiter()
        now = time.time()
        limiter.requests["old"] = [now - 70]
        limiter.requests["new"] = [now - 10]
        
        limiter._cleanup(now)
        
        assert "old" not in limiter.requests
        assert "new" in limiter.requests

    def test_get_rate_limit_key(self):
        """Test generating key from request."""
        mock_request = MagicMock()
        mock_request.client.host = "1.2.3.4"
        assert get_rate_limit_key(mock_request) == "ip:1.2.3.4"
        
        mock_request.client = None
        assert get_rate_limit_key(mock_request) == "ip:unknown"

    def test_check_rate_limit_raises(self, monkeypatch):
        """Test that check_rate_limit raises HTTPException when exceeded."""
        from fastapi import HTTPException
        from app.utils import rate_limiter
        
        mock_limiter = MagicMock()
        mock_limiter.is_allowed.return_value = False
        monkeypatch.setattr(rate_limiter, "_remote_rate_limiter", mock_limiter)
        
        mock_request = MagicMock()
        with pytest.raises(HTTPException) as exc:
            check_rate_limit(mock_request)
        assert exc.value.status_code == 429
