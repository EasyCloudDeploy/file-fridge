"""Error classification utilities for remote transfers."""

import random
from enum import Enum
from typing import Optional

import httpx


class TransferErrorType(Enum):
    """Classification of transfer errors."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RATE_LIMITED = "rate_limited"


class TransferRetryStrategy:
    """Retry strategy with exponential backoff and jitter."""

    def __init__(
        self,
        max_retries: int = 5,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 300.0,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay_seconds
        self.max_delay = max_delay_seconds

    def should_retry(
        self, attempt: int, error: Optional[Exception] = None
    ) -> tuple[bool, float, str]:
        """
        Determine if operation should retry and calculate delay.

        Args:
            attempt: Current attempt number (1-indexed)
            error: The exception that occurred

        Returns:
            Tuple of (should_retry, delay_seconds, reason)
        """
        if attempt >= self.max_retries:
            return False, 0, "Max retries exceeded"

        error_type = self.classify_error(error)

        if error_type == TransferErrorType.PERMANENT:
            return False, 0, "Permanent error, should not retry"

        if error_type == TransferErrorType.RATE_LIMITED:
            # For rate limits, use longer delay
            delay = min(self.base_delay * (2**attempt) * 3, self.max_delay)
            return True, delay, f"Rate limited, retrying in {delay:.0f}s"

        # Transient errors: exponential backoff with jitter
        delay = min(self.base_delay * (2**attempt), self.max_delay)
        jitter = random.uniform(0, delay * 0.1)  # Add up to 10% jitter
        actual_delay = delay + jitter

        return (
            True,
            actual_delay,
            f"Transient error, retrying in {actual_delay:.1f}s (attempt {attempt + 1}/{self.max_retries})",
        )

    @staticmethod
    def classify_error(error: Optional[Exception]) -> TransferErrorType:
        """
        Classify an error as transient or permanent.

        Args:
            error: The exception that occurred

        Returns:
            TransferErrorType classification
        """
        if error is None:
            return TransferErrorType.TRANSIENT

        # Network-level errors - transient
        if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
            return TransferErrorType.TRANSIENT

        # HTTP errors
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code

            # Rate limiting - retry with delay
            if status_code in (429, 503):
                return TransferErrorType.RATE_LIMITED

            # 5xx server errors - transient
            if 500 <= status_code < 600:
                return TransferErrorType.TRANSIENT

            # 4xx client errors (except 429) - permanent
            if 400 <= status_code < 500:
                return TransferErrorType.PERMANENT

        # Other exceptions - treat as transient
        return TransferErrorType.TRANSIENT


retry_strategy = TransferRetryStrategy()
