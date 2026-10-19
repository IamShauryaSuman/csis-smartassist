"""
Production Safeguards — In-memory Sliding Window Rate Limiter Middleware.

Protects LLM inference, booking creation, and RAG sync endpoints from abuse
and denial-of-service, returning RFC-compliant HTTP 429 Too Many Requests responses
with RateLimit headers (X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Callable, NamedTuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitRule(NamedTuple):
    limit: int
    window_seconds: int


# Default production rate limits per IP / User identifier:
# - /api/chat/send: 20 requests per minute
# - /api/bookings (POST/PATCH): 15 requests per minute
# - /api/rag/sync-file: 60 requests per minute
DEFAULT_RULES: dict[str, RateLimitRule] = {
    "/api/chat/send": RateLimitRule(limit=20, window_seconds=60),
    "/api/bookings": RateLimitRule(limit=15, window_seconds=60),
    "/api/rag/sync-file": RateLimitRule(limit=60, window_seconds=60),
}


class SlidingWindowRateLimiter:
    """Sliding-window counter rate limiter with thread-safe async locks."""

    def __init__(self, rules: dict[str, RateLimitRule] | None = None):
        self.rules = rules or DEFAULT_RULES
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _get_matching_rule(self, path: str, method: str) -> tuple[str, RateLimitRule] | None:
        """Find matching rule for path, taking HTTP methods into account."""
        # For /api/bookings, only rate limit write operations (POST, PATCH)
        if path == "/api/bookings" and method not in ("POST", "PATCH"):
            return None

        for endpoint, rule in self.rules.items():
            if path == endpoint or path.startswith(endpoint + "/"):
                return endpoint, rule
        return None

    async def check_rate_limit(
        self, client_id: str, path: str, method: str
    ) -> tuple[bool, int, int, int]:
        """Check whether the client request is permitted under rate limits.

        Returns:
            (is_allowed, limit, remaining, retry_after)
        """
        match = self._get_matching_rule(path, method)
        if not match:
            return True, -1, -1, 0

        endpoint, rule = match
        now = time.time()
        window_start = now - rule.window_seconds
        key = f"{client_id}:{endpoint}"

        async with self._lock:
            timestamps = self._history[key]
            # Prune timestamps outside window
            valid_timestamps = [t for t in timestamps if t > window_start]
            self._history[key] = valid_timestamps

            count = len(valid_timestamps)
            if count >= rule.limit:
                # Rate limit exceeded: calculate retry after
                oldest_timestamp = valid_timestamps[0]
                retry_after = max(1, int(oldest_timestamp + rule.window_seconds - now))
                return False, rule.limit, 0, retry_after

            # Allow request and append timestamp
            self._history[key].append(now)
            remaining = rule.limit - (count + 1)
            return True, rule.limit, remaining, 0

    async def clear(self) -> None:
        """Clear limiter state (used in testing)."""
        async with self._lock:
            self._history.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI / Starlette middleware enforcing sliding-window rate limits."""

    def __init__(self, app, limiter: SlidingWindowRateLimiter | None = None):
        super().__init__(app)
        self.limiter = limiter or SlidingWindowRateLimiter()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Determine client identifier: prioritize auth token / user ID, fallback to client IP
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            client_id = auth_header[7:].strip()[:32]
        else:
            client_id = request.client.host if request.client else "127.0.0.1"

        path = request.url.path
        method = request.method

        is_allowed, limit, remaining, retry_after = await self.limiter.check_rate_limit(
            client_id, path, method
        )

        if not is_allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please wait before retrying.",
                    "retry_after": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)

        if limit > 0:
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
