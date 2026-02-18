import pytest
from app.utils.rate_limiter import _login_rate_limiter

def test_login_rate_limit_bypass(client):
    """
    Test that login rate limit CANNOT be bypassed by rotating X-Instance-UUID or X-Forwarded-For.
    """
    url = "/api/v1/auth/login"
    payload = {"username": "admin", "password": "wrong-password-for-test"}  # NOSONAR

    # Reset state
    _login_rate_limiter.requests.clear()

    # Fill the bucket (limit is 5)
    for i in range(5):
        resp = client.post(url, json=payload)
        assert resp.status_code == 401

    # 6th request should fail (Baseline check)
    response = client.post(url, json=payload)
    assert response.status_code == 429, "Rate limit not working at all!"

    # ATTEMPT BYPASS with X-Instance-UUID
    # This header causes get_rate_limit_key to return "remote:spoofed-uuid-123"
    # instead of "ip:testclient", effectively giving a fresh bucket.
    response = client.post(
        url,
        json=payload,
        headers={"X-Instance-UUID": "spoofed-uuid-123"}
    )

    if response.status_code != 429:
        pytest.fail(f"VULNERABILITY CONFIRMED: Rate limit bypassed via X-Instance-UUID (Got {response.status_code})")

    # ATTEMPT BYPASS with X-Forwarded-For
    # This header causes get_rate_limit_key to return "ip:1.2.3.4"
    # instead of "ip:testclient", effectively giving a fresh bucket.
    response = client.post(
        url,
        json=payload,
        headers={"X-Forwarded-For": "1.2.3.4"}
    )

    if response.status_code != 429:
         pytest.fail(f"VULNERABILITY CONFIRMED: Rate limit bypassed via X-Forwarded-For (Got {response.status_code})")
