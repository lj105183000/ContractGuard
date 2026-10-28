"""节点进度事件。

图节点不直接依赖 JobStore，而是通过 job_id 发事件；API 层订阅后写入 JobStore
（内存缓存 + SQLite 快照）。
没有订阅者（例如评测脚本）时，事件是 no-op。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from app.schemas import EventStatus, JobEvent

Listener = Callable[[JobEvent], None]
_listeners: dict[str, list[Listener]] = defaultdict(list)


def subscribe(job_id: str, listener: Listener) -> None:
    _listeners[job_id].append(listener)


def unsubscribe(job_id: str) -> None:
    _listeners.pop(job_id, None)


def emit(
    job_id: str,
    node: str,
    label: str,
    status: EventStatus,
    message: str = "",
    progress: int = 0,
    duration_ms: int = 0,
) -> None:
    event = JobEvent(
        node=node,
        label=label,
        status=status,
        message=message,
        progress=progress,
        duration_ms=duration_ms,
    )
    for listener in list(_listeners.get(job_id, [])):
        try:
            listener(event)
        except Exception:  # noqa: BLE001
            # 进度回调失败不应中断审查
            pass
