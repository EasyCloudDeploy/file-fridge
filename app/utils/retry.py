"""Retry utility for resilient file operations."""

import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    allowed_exceptions: tuple = (Exception,),
) -> Callable:
    """Decorator to retry a function call with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts before raising.
        initial_delay: Initial sleep delay in seconds.
        backoff_factor: Multiplier applied to delay after each failure.
        allowed_exceptions: Tuple of exception types to catch and retry.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = initial_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except allowed_exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts. Error: {e}"
                        )
                        raise
                    logger.warning(
                        f"Function {func.__name__} failed on attempt {attempt}/{max_attempts} with error: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
            raise RuntimeError("Unexpected end of retry loop")

        return wrapper

    return decorator
