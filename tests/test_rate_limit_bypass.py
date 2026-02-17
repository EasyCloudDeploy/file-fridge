import pytest
from fastapi import status
from app.utils.rate_limiter import _login_rate_limiter

def test_login_rate_limit_bypass(client):
    """
    Test that login rate limit CANNOT be bypassed by rotating X-Instance-UUID or X-Forwarded-For.
    """
    url = "/api/v1/auth/login"

    # Reset state
    _login_rate_limiter.requests.clear()

    # Fill the bucket (limit is 5)
    for _ in range(5):
        resp = client.post(url, json={"username": "admin", "password": "wrong"})  # NOSONAR
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    # 6th request should fail (Baseline check)
    response = client.post(url, json={"username": "admin", "password": "wrong"})  # NOSONAR
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit not working at all!"

    # ATTEMPT BYPASS with X-Instance-UUID
    # This header causes get_rate_limit_key to return "remote:spoofed-uuid-123"
    # instead of "ip:testclient", effectively giving a fresh bucket.
    response = client.post(
        url,
        json={"username": "admin", "password": "wrong"},  # NOSONAR
        headers={"X-Instance-UUID": "spoofed-uuid-123"}
    )

    if response.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
        pytest.fail(f"VULNERABILITY CONFIRMED: Rate limit bypassed via X-Instance-UUID (Got {response.status_code})")

    # ATTEMPT BYPASS with X-Forwarded-For
    # This header causes get_rate_limit_key to return "ip:1.2.3.4"
    # instead of "ip:testclient", effectively giving a fresh bucket.
    response = client.post(
        url,
        json={"username": "admin", "password": "wrong"},  # NOSONAR
        headers={"X-Forwarded-For": "1.2.3.4"}
    )

    if response.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
         pytest.fail(f"VULNERABILITY CONFIRMED: Rate limit bypassed via X-Forwarded-For (Got {response.status_code})")
