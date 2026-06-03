from unittest.mock import patch

import pytest

from app.utils.retry import retry_with_backoff


@pytest.mark.unit
class TestRetryWithBackoff:
    def test_success_on_first_try(self):
        """Test that a function that succeeds immediately returns immediately without retrying."""
        calls = 0

        @retry_with_backoff(max_attempts=3, initial_delay=0.1, backoff_factor=2.0)
        def func(x):
            nonlocal calls
            calls += 1
            return x * 2

        result = func(5)
        assert result == 10
        assert calls == 1

    def test_success_after_failure(self):
        """Test that a function that fails initially but succeeds later is retried and succeeds."""
        calls = 0

        @retry_with_backoff(max_attempts=3, initial_delay=0.1, backoff_factor=2.0, allowed_exceptions=(ValueError,))
        def func():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ValueError("Temporary failure")
            return "success"

        with patch("time.sleep") as mock_sleep:
            result = func()
            assert result == "success"
            assert calls == 3
            assert mock_sleep.call_count == 2
            # Check delay progression: initial_delay, then initial_delay * backoff_factor
            mock_sleep.assert_any_call(0.1)
            mock_sleep.assert_any_call(0.2)

    def test_fail_after_max_attempts(self):
        """Test that allowed exceptions raise the original error after max_attempts."""
        calls = 0

        @retry_with_backoff(max_attempts=3, initial_delay=0.1, backoff_factor=2.0, allowed_exceptions=(ValueError,))
        def func():
            nonlocal calls
            calls += 1
            raise ValueError(f"Failure {calls}")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(ValueError, match="Failure 3"):
                func()
            assert calls == 3
            assert mock_sleep.call_count == 2

    def test_raise_unallowed_exception_immediately(self):
        """Test that exceptions not listed in allowed_exceptions raise immediately without retries."""
        calls = 0

        @retry_with_backoff(max_attempts=3, initial_delay=0.1, backoff_factor=2.0, allowed_exceptions=(ValueError,))
        def func():
            nonlocal calls
            calls += 1
            raise KeyError("Key error")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(KeyError, match="Key error"):
                func()
            assert calls == 1
            mock_sleep.assert_not_called()
