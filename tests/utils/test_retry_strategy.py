import pytest
import httpx
from unittest.mock import MagicMock

from app.utils.retry_strategy import TransferRetryStrategy, TransferErrorType, retry_strategy


@pytest.mark.unit
class TestTransferRetryStrategy:
    def test_classify_error_types(self):
        """Test error classification for various exception types."""
        # 1. Transient: Timeouts and Network errors
        assert retry_strategy.classify_error(httpx.ConnectTimeout("Error")) == TransferErrorType.TRANSIENT
        assert retry_strategy.classify_error(httpx.ReadTimeout("Error")) == TransferErrorType.TRANSIENT
        assert retry_strategy.classify_error(httpx.NetworkError("Error")) == TransferErrorType.TRANSIENT
        
        # 2. Transient: 5xx Server Errors (except 503 handled below)
        mock_500 = MagicMock(status_code=500)
        err_500 = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_500)
        assert retry_strategy.classify_error(err_500) == TransferErrorType.TRANSIENT
        
        # 3. Permanent: 4xx Client Errors (except 429)
        mock_404 = MagicMock(status_code=404)
        err_404 = httpx.HTTPStatusError("404", request=MagicMock(), response=mock_404)
        assert retry_strategy.classify_error(err_404) == TransferErrorType.PERMANENT
        
        # 4. Rate Limited: 429 and 503
        for code in [429, 503]:
            mock_resp = MagicMock(status_code=code)
            err = httpx.HTTPStatusError(str(code), request=MagicMock(), response=mock_resp)
            assert retry_strategy.classify_error(err) == TransferErrorType.RATE_LIMITED
            
        # 5. Other: defaults to transient
        assert retry_strategy.classify_error(RuntimeError("Unknown")) == TransferErrorType.TRANSIENT
        assert retry_strategy.classify_error(None) == TransferErrorType.TRANSIENT

    def test_should_retry_logic(self):
        """Test the retry decision and delay calculation."""
        strategy = TransferRetryStrategy(max_retries=3, base_delay_seconds=1.0)
        
        # Attempt 1 (transient)
        retry, delay, reason = strategy.should_retry(1, httpx.ConnectTimeout("T"))
        assert retry is True
        assert 1.0 <= delay <= 2.2 # base * 2^1 + 10% jitter
        assert "retrying" in reason.lower()
        
        # Attempt 1 (rate limited)
        retry, delay, reason = strategy.should_retry(1, httpx.HTTPStatusError("429", request=MagicMock(), response=MagicMock(status_code=429)))
        assert retry is True
        assert delay >= 6.0 # base * 2^1 * 3
        
        # Attempt 1 (permanent)
        mock_400 = MagicMock(status_code=400)
        err_400 = httpx.HTTPStatusError("400", request=MagicMock(), response=mock_400)
        retry, delay, reason = strategy.should_retry(1, err_400)
        assert retry is False
        assert "permanent" in reason.lower()
        
        # Max retries exceeded
        retry, delay, reason = strategy.should_retry(3, None)
        assert retry is False
        assert "exceeded" in reason.lower()
