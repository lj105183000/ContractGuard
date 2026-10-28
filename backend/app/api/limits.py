"""Optional in-process request guard.

This is a single-process sliding-window limiter meant for a small single-tenant
deployment. For multi-process or public deployments, terminate at a reverse
proxy with a shared rate limiter.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import settings

_WINDOW_S = 60.0
_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(request: Request) -> None:
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _buckets[key]
    while bucket and now - bucket[0] >= _WINDOW_S:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(_WINDOW_S - (now - bucket[0])))
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试。",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


def reset_rate_limits() -> None:
    _buckets.clear()
