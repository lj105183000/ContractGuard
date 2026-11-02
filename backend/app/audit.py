"""Minimal deletion audit trail (tombstones).

Only opaque job ids and deletion metadata are recorded; no contract text,
filenames, notes, or model output. This gives operators a way to explain what
was deleted and why without retaining the deleted payload.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import AUDIT_DIR

logger = logging.getLogger(__name__)

FORMAT = "contractguard.deletion_tombstone"
VERSION = 1


def append_deletion(
    job_id: str,
    *,
    reason: str,
    status: str,
    had_result: bool,
    path: Path | None = None,
) -> None:
    try:
        target = Path(path or AUDIT_DIR)
        target.mkdir(parents=True, exist_ok=True)
        record = {
            "format": FORMAT,
            "version": VERSION,
            "job_id": job_id,
            "deleted_at": datetime.now(timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds"),
            "reason": reason,
            "status": status,
            "had_result": bool(had_result),
        }
        with (target / "deletions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("写入删除墓碑失败 %s：%s", job_id, exc)
