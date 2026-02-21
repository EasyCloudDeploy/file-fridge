
import pytest
from fastapi import FastAPI, Request, Depends
from fastapi.testclient import TestClient
from app.utils.rate_limiter import check_login_rate_limit, _login_rate_limiter

# Create a dummy app for testing
app = FastAPI()

@app.get("/login_test", dependencies=[Depends(check_login_rate_limit)])
def login_test():
    return {"message": "ok"}

client = TestClient(app)

def test_rate_limit_bypass_x_forwarded_for():
    # Reset the rate limiter
    _login_rate_limiter.requests.clear()

    # Fill the bucket (limit is 5 requests per minute)
    for _ in range(5):
        response = client.get("/login_test", headers={"X-Forwarded-For": "1.2.3.4"})  # NOSONAR
        assert response.status_code == 200

    # The next request should fail
    response = client.get("/login_test", headers={"X-Forwarded-For": "1.2.3.4"})  # NOSONAR
    assert response.status_code == 429

    # Now try to bypass with a different IP in X-Forwarded-For
    response = client.get("/login_test", headers={"X-Forwarded-For": "5.6.7.8"})  # NOSONAR

    # With the fix, the rate limiter should ignore X-Forwarded-For and use the client's real IP.
    # Since the client IP is constant ("testclient") for TestClient, and the bucket was filled
    # by previous requests (even if they had XFF headers, they were counted against "testclient"),
    # this request should be blocked.

    # Note: In the previous implementation, the first 5 requests counted against "ip:1.2.3.4".
    # With the fix, they count against "ip:testclient".
    # So after 5 requests, "ip:testclient" bucket is full.
    # The 6th request (with different XFF) should still count against "ip:testclient" and be blocked.

    assert response.status_code == 429, "Rate limit bypassed via X-Forwarded-For spoofing"

def test_rate_limit_bypass_x_instance_uuid():
    # Reset
    _login_rate_limiter.requests.clear()

    # Fill bucket using one UUID (but actually using client IP with the fix)
    for _ in range(5):
        response = client.get("/login_test", headers={"X-Instance-UUID": "uuid-1"})  # NOSONAR
        assert response.status_code == 200

    # Next one fails
    response = client.get("/login_test", headers={"X-Instance-UUID": "uuid-1"})  # NOSONAR
    assert response.status_code == 429

    # Attempt Bypass with new UUID
    # With the fix, X-Instance-UUID is ignored, so it still counts against client IP
    response = client.get("/login_test", headers={"X-Instance-UUID": "uuid-2"})  # NOSONAR
    assert response.status_code == 429, "Rate limit bypassed via X-Instance-UUID spoofing"
