import hashlib
import secrets
import threading


class RemoteAuth:
    """Manages hourly rotating codes for remote instance connections."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._code = cls._instance._generate_code()
            return cls._instance

    def _generate_code(self) -> str:
        """Generate a random SHA256 hash."""
        random_bytes = secrets.token_bytes(32)
        return hashlib.sha256(random_bytes).hexdigest()

    def rotate_code(self):
        """Rotate the code."""
        with self._lock:
            self._code = self._generate_code()

    def get_code(self) -> str:
        """Get the current code."""
        with self._lock:
            return self._code


remote_auth = RemoteAuth()
