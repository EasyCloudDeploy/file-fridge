import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone


class RemoteAuth:
    """Manages hourly rotating codes for remote instance connections."""

    _instance = None
    _lock = threading.Lock()
    CODE_EXPIRY_HOURS = 1

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._code = cls._instance._generate_code()
                cls._instance._generated_at = datetime.now(timezone.utc)
            return cls._instance

    def _generate_code(self) -> str:
        """Generate a random SHA256 hash."""
        random_bytes = secrets.token_bytes(32)
        return hashlib.sha256(random_bytes).hexdigest()

    def rotate_code(self):
        """Rotate the code."""
        with self._lock:
            self._code = self._generate_code()
            self._generated_at = datetime.now(timezone.utc)

    def get_code(self) -> str:
        """Get the current code."""
        with self._lock:
            return self._code

    def get_code_with_expiry(self) -> tuple[str, int]:
        """
        Get the current code and seconds until expiration.

        Returns:
            tuple[str, int]: (code, seconds_until_expiration)
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            expires_at = self._generated_at + timedelta(hours=self.CODE_EXPIRY_HOURS)
            expires_in_seconds = int((expires_at - now).total_seconds())
            return self._code, max(0, expires_in_seconds)


remote_auth = RemoteAuth()
