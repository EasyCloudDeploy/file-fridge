"""Circuit breaker for failing remote connections."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker to stop hitting failing remote instances."""

    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half-open

    def can_attempt(self) -> bool:
        """Check if we can attempt a connection."""
        if self.state == "open":
            # Check if timeout has passed
            if (
                self.last_failure_time
                and time.time() - self.last_failure_time > self.timeout_seconds
            ):
                self.state = "half-open"
                logger.info(f"Circuit breaker entering half-open state")
                return True
            return False
        return True

    def record_success(self):
        """Record a successful connection."""
        self.failure_count = 0
        self.last_failure_time = None
        if self.state in ("open", "half-open"):
            self.state = "closed"
            logger.info("Circuit breaker reset to closed state")

    def record_failure(self):
        """Record a failed connection."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            if self.state != "open":
                self.state = "open"
                logger.warning(
                    f"Circuit breaker opened after {self.failure_threshold} failures, "
                    f"will skip for {self.timeout_seconds} seconds"
                )
        else:
            logger.debug(
                f"Circuit breaker failure count: {self.failure_count}/{self.failure_threshold}"
            )


# Circuit breakers indexed by connection ID
_circuit_breakers: dict[int, CircuitBreaker] = {}


def get_circuit_breaker(connection_id: int) -> CircuitBreaker:
    """Get or create circuit breaker for a connection."""
    if connection_id not in _circuit_breakers:
        _circuit_breakers[connection_id] = CircuitBreaker()
    return _circuit_breakers[connection_id]


def reset_circuit_breaker(connection_id: int) -> None:
    """Reset circuit breaker for a connection."""
    if connection_id in _circuit_breakers:
        _circuit_breakers[connection_id] = CircuitBreaker()
        logger.info(f"Circuit breaker reset for connection {connection_id}")
