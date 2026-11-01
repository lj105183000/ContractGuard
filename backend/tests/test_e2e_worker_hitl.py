"""默认运行形态（worker + HITL + 异步）的端到端回归。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.jobs import store

SAMPLE_ID = "sample_01"


def _get_state(client: TestClient, job_id: str) -> dict:
    response = client.get(f"/api/reviews/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for(
    client: TestClient, job_id: str, statuses: set[str], timeout: float = 60.0
) -> dict:
    deadline = time.monotonic() + timeout
    state = _get_state(client, job_id)
    while state["status"] not in statuses and time.monotonic() < deadline:
        time.sleep(0.1)
        state = _get_state(client, job_id)
    assert state["status"] in statuses, state
    return state


def test_default_worker_hitl_end_to_end(tmp_path):
    """上传 → worker 审查 → HITL 暂停 → 决策 → worker resume → 导出。"""
    names = (
        "worker_enabled",
        "sync_jobs",
        "hitl_enabled",
        "hitl_resume_on_startup",
        "checkpoint_db_path",
    )
    old_values = {name: getattr(settings, name) for name in names}
    object.__setattr__(settings, "worker_enabled", True)
    object.__setattr__(settings, "sync_jobs", False)
    object.__setattr__(settings, "hitl_enabled", True)
    object.__setattr__(settings, "hitl_resume_on_startup", False)
    object.__setattr__(
        settings, "checkpoint_db_path", str(tmp_path / "checkpoints.sqlite3")
    )
    store.reset()
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/reviews/samples/{SAMPLE_ID}")
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]

            state = _wait_for(client, job_id, {"awaiting_review"})
            pending_ids = list(state.get("pending_review_ids") or [])
            assert pending_ids, state
            assert state["result"] is not None

            for finding_id in pending_ids:
                decision = client.post(
                    f"/api/reviews/{job_id}/findings/{finding_id}/decision",
                    json={"decision": "accepted", "note": "e2e worker hitl"},
                )
                assert decision.status_code == 200, decision.text

            state = _wait_for(
                client, job_id, {"completed", "completed_with_warnings"}, timeout=60
            )
            assert state["result"] is not None

            report = client.get(f"/api/reviews/{job_id}/report?format=md")
            assert report.status_code == 200, report.text
            revised = client.get(
                f"/api/reviews/{job_id}/revised-contract?format=json"
            )
            assert revised.status_code == 200, revised.text
    finally:
        for name, value in old_values.items():
            object.__setattr__(settings, name, value)
