"""Minimal in-process request metrics for the local/demo deployment."""

from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_requests = Counter()
_statuses = Counter()
_total = 0
_total_duration_ms = 0


def record_request(method: str, status_code: int, duration_ms: int) -> None:
    global _total, _total_duration_ms
    with _lock:
        _total += 1
        _total_duration_ms += max(0, duration_ms)
        _requests[method] += 1
        _statuses[str(status_code)] += 1


def metrics_snapshot() -> dict:
    with _lock:
        total = _total
        duration = _total_duration_ms
        return {
            "requests_total": total,
            "request_duration_ms_avg": round(duration / total, 1) if total else 0,
            "requests_by_method": dict(sorted(_requests.items())),
            "responses_by_status": dict(sorted(_statuses.items())),
        }


def reset_metrics() -> None:
    global _total, _total_duration_ms
    with _lock:
        _requests.clear()
        _statuses.clear()
        _total = 0
        _total_duration_ms = 0
