import pytest
import time
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.rate_limiter import SlidingWindowRateLimiter, RateLimitMiddleware, RateLimitRule

@pytest.fixture
def rate_limited_app():
    test_app = FastAPI()
    limiter = SlidingWindowRateLimiter(rules={
        "/api/test-limit": RateLimitRule(limit=3, window_seconds=2),
    })
    test_app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @test_app.get("/api/test-limit")
    def test_endpoint():
        return {"status": "ok"}

    @test_app.get("/api/unlimited")
    def unlimited_endpoint():
        return {"status": "ok"}

    return test_app

def test_rate_limiter_allows_under_limit(rate_limited_app):
    client = TestClient(rate_limited_app)

    # 3 requests allowed within 2 seconds
    for i in range(3):
        res = client.get("/api/test-limit")
        assert res.status_code == 200
        assert "X-RateLimit-Limit" in res.headers
        assert res.headers["X-RateLimit-Limit"] == "3"
        assert res.headers["X-RateLimit-Remaining"] == str(2 - i)

def test_rate_limiter_blocks_over_limit(rate_limited_app):
    client = TestClient(rate_limited_app)

    for _ in range(3):
        res = client.get("/api/test-limit")
        assert res.status_code == 200

    # 4th request exceeds limit
    res4 = client.get("/api/test-limit")
    assert res4.status_code == 429
    assert res4.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in res4.headers
    assert res4.json()["detail"] == "Rate limit exceeded. Please wait before retrying."

def test_rate_limiter_skips_unconfigured_endpoints(rate_limited_app):
    client = TestClient(rate_limited_app)

    for _ in range(10):
        res = client.get("/api/unlimited")
        assert res.status_code == 200
        assert "X-RateLimit-Limit" not in res.headers
