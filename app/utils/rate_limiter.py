import time
from collections import defaultdict
from functools import wraps
from typing import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    """Simple in-memory rate limiter using token bucket algorithm."""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)
        self.cleanup_interval = 60  # Clean up old records every minute
        self.last_cleanup = time.time()

    def is_allowed(self, identifier: str) -> bool:
        """Check if request is allowed for given identifier."""
        now = time.time()

        # Clean up old records periodically
        if now - self.last_cleanup > self.cleanup_interval:
            self._cleanup(now)

        # Get requests for this identifier
        user_requests = self.requests[identifier]

        # Remove requests older than 1 minute
        cutoff = now - 60
        user_requests[:] = [t for t in user_requests if t > cutoff]

        # Check if under limit
        if len(user_requests) < self.requests_per_minute:
            user_requests.append(now)
            return True
        return False

    def _cleanup(self, now: float):
        """Remove stale records from all users."""
        cutoff = now - 60
        stale_users = []
        for user_id, timestamps in self.requests.items():
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if not timestamps:
                stale_users.append(user_id)

        for user_id in stale_users:
            del self.requests[user_id]

        self.last_cleanup = now


# Global rate limiter instance
_remote_rate_limiter = RateLimiter(requests_per_minute=100)

# Global rate limiter for login
_login_rate_limiter = RateLimiter(requests_per_minute=5)


def get_rate_limit_key(request: Request) -> str:
    """Extract rate limit key from request."""
    # Use remote instance UUID for authenticated remote connections
    x_instance_uuid = request.headers.get("X-Instance-UUID")
    if x_instance_uuid:
        return f"remote:{x_instance_uuid}"

    # Use IP address for other requests
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"


def check_rate_limit(request: Request) -> None:
    """Check rate limit and raise HTTPException if exceeded."""
    key = get_rate_limit_key(request)
    if not _remote_rate_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
            headers={"Retry-After": "60"},
        )


def check_login_rate_limit(request: Request) -> None:
    """Check login rate limit and raise HTTPException if exceeded."""
    key = get_rate_limit_key(request)
    if not _login_rate_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": "60"},
        )


def rate_limit(requests_per_minute: int = 60):
    """Decorator to rate limit an endpoint."""
    limiter = RateLimiter(requests_per_minute=requests_per_minute)

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            for arg in kwargs.values():
                if isinstance(arg, Request):
                    request = arg
                    break

            if request:
                key = get_rate_limit_key(request)
                if not limiter.is_allowed(key):
                    raise HTTPException(
                        status_code=429,
                        detail="Rate limit exceeded. Please try again later.",
                        headers={"Retry-After": "60"},
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
