import pytest
from app.utils.sanitization import sanitize_for_log


@pytest.mark.unit
class TestSanitization:
    def test_sanitize_for_log_basic(self):
        """Test sanitizing basic control characters."""
        # Use escaped backslashes to avoid interpolation by write_file tool
        assert sanitize_for_log("hello\nworld") == "hello\\x0aworld"
        assert sanitize_for_log("tab\tseparated") == "tab\\x09separated"
        assert sanitize_for_log("carriage\rreturn") == "carriage\\x0dreturn"

    def test_sanitize_for_log_none_empty(self):
        """Test handling of None and empty strings."""
        assert sanitize_for_log(None) == ""
        assert sanitize_for_log("") == ""

    def test_sanitize_for_log_safe(self):
        """Test that safe strings are unchanged."""
        safe = "This is a safe string 123 !@#$%^&*()"
        assert sanitize_for_log(safe) == safe

    def test_sanitize_for_log_non_string(self):
        """Test handling of non-string inputs."""
        assert sanitize_for_log(123) == "123"
