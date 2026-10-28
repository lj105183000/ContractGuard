"""SQLite lease worker: exclusivity, heartbeats, reaping and end-to-end run."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.config import settings
from app.crypto import STATE_PREFIX
from app.main import app
from app.schemas import (
    Category,
    ConfidenceTier,
    ContractMeta,
    Decision,
    Finding,
    JobStatus,
    ReviewResult,
    Severity,
    Source,
)
from app.services.jobs import (
    JobResuming,
    JobStore,
    LeaseLost,
    store,
)
from app.services.summary import build_summary
from app.worker import WorkerPool

SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "samples"
    / "sample_01_数控设备采购合同.docx"
)


def _finding(finding_id: str = "f1") -> Finding:
    return Finding(
        id=finding_id,
        category=Category.PAYMENT,
        severity=Severity.HIGH,
        source=Source.RULE,
        title="预付款比例过高",
        confidence_tier=ConfidenceTier.CONFIRMED,
    )


def _report(*findings: Finding) -> ReviewResult:
    return ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary(list(findings)),
        clauses=[],
        findings=list(findings),
    )


def _expire_lease(target: JobStore, job_id: str) -> None:
    target._connection().execute(
        "UPDATE jobs SET lease_expires_at = 0 WHERE job_id = ?", (job_id,)
    )


def _row(target: JobStore, job_id: str):
    return target._connection().execute(
        "SELECT status, resume_requested, attempt, max_attempts, lease_token "
        "FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()


def test_claim_is_exclusive_and_requires_ready(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    second = JobStore(db_path)
    try:
        job = first.create("contract.txt")
        assert first.claim("worker-1", 60) is None
        assert first.mark_ready(job.job_id)

        claim = first.claim("worker-1", 60)
        assert claim is not None
        assert claim.job.job_id == job.job_id
        assert claim.kind == "review"
        assert claim.job.status == JobStatus.RUNNING
        assert second.claim("worker-2", 60) is None

        # claim 只更新列，get(fresh=True) 必须看到 DB 上的 running。
        fresh = second.get(job.job_id, fresh=True)
        assert fresh.status == JobStatus.RUNNING

        first.heartbeat(job.job_id, claim.token)
        first.release(job.job_id, claim.token)
        with pytest.raises(LeaseLost):
            first.heartbeat(job.job_id, claim.token)
    finally:
        first.close()
        second.close()


def test_reaper_fails_expired_running_job(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    second = JobStore(db_path)
    try:
        job = first.create("contract.txt")
        first.mark_ready(job.job_id)
        claim = first.claim("worker-1", 60)
        assert claim is not None
        _expire_lease(first, job.job_id)

        second.reap_expired_leases()
        restored = second.get(job.job_id, fresh=True)
        assert restored.status == JobStatus.FAILED
        assert "租约" in (restored.error or "")
    finally:
        first.close()
        second.close()


def test_reaper_retries_resuming_until_max_attempts(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    worker_store = JobStore(db_path)
    reaper_store = JobStore(db_path)
    try:
        finding = _finding()
        job = worker_store.create("contract.txt")
        assert worker_store.mark_ready(job.job_id)
        worker_store.set_awaiting_review(
            job.job_id, _report(finding), [], "interrupt-1", [finding.id]
        )
        assert worker_store.request_resume(job.job_id)

        for attempt in (1, 2):
            claim = worker_store.claim("worker-1", 60)
            assert claim is not None and claim.kind == "resume"
            assert _row(worker_store, job.job_id)["attempt"] == attempt
            _expire_lease(worker_store, job.job_id)
            reaper_store.reap_expired_leases()
            row = _row(worker_store, job.job_id)
            assert row["status"] == JobStatus.AWAITING_REVIEW.value
            assert row["resume_requested"] == 1
            assert worker_store.get(job.job_id, fresh=True).status == (
                JobStatus.AWAITING_REVIEW
            )

        claim = worker_store.claim("worker-1", 60)
        assert claim is not None and claim.kind == "resume"
        assert _row(worker_store, job.job_id)["attempt"] == 3
        _expire_lease(worker_store, job.job_id)
        reaper_store.reap_expired_leases()
        restored = worker_store.get(job.job_id, fresh=True)
        assert restored.status == JobStatus.FAILED
        assert "租约" in (restored.error or "")
    finally:
        worker_store.close()
        reaper_store.close()


def test_failed_job_queues_checkpoint_cleanup(tmp_path):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = job_store.create("contract.txt")
        job_store.set_failed(job.job_id, "boom")
        assert job.job_id in job_store.pop_checkpoint_cleanup()
    finally:
        job_store.close()


def test_reaper_fails_stale_queued_upload(tmp_path):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = job_store.create("contract.txt")
        job_store._connection().execute(
            "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+08:00' "
            "WHERE job_id = ?",
            (job.job_id,),
        )
        job_store.reap_expired_leases()
        restored = job_store.get(job.job_id, fresh=True)
        assert restored.status == JobStatus.FAILED
        assert "排队超时" in (restored.error or "")
    finally:
        job_store.close()


def test_reaper_keeps_ready_queued_backlog(tmp_path):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = job_store.create("contract.txt")
        assert job_store.mark_ready(job.job_id)
        job_store._connection().execute(
            "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+08:00' "
            "WHERE job_id = ?",
            (job.job_id,),
        )

        job_store.reap_expired_leases()

        row = _row(job_store, job.job_id)
        assert row["status"] == JobStatus.QUEUED.value
        assert row["lease_token"] is None
    finally:
        job_store.close()


def test_reaper_rejects_stale_revision_write(tmp_path):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = job_store.create("contract.txt")
        revision = job_store._connection().execute(
            "SELECT revision FROM jobs WHERE job_id = ?", (job.job_id,)
        ).fetchone()[0]
        stale = job_store._load(job.job_id)
        assert stale is not None
        job_store._connection().execute(
            "UPDATE jobs SET revision = revision + 1 WHERE job_id = ?",
            (job.job_id,),
        )
        stale.status = JobStatus.FAILED
        stale.error = "stale"
        stale.updated_at = job_store._now()

        persisted = job_store._persist_reaped(
            stale,
            expected_status=JobStatus.QUEUED.value,
            expected_revision=revision,
            require_expired_lease=False,
        )

        assert not persisted
        assert job_store.get(job.job_id, fresh=True).status == JobStatus.QUEUED
    finally:
        job_store.close()


def test_pause_resets_resume_attempts(tmp_path):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        finding = _finding()
        job = job_store.create("contract.txt")
        assert job_store.mark_ready(job.job_id)
        claim = job_store.claim("worker-1", 60)
        assert claim is not None and claim.kind == "review"
        assert _row(job_store, job.job_id)["attempt"] == 1

        job_store.set_awaiting_review(
            job.job_id, _report(finding), [], "interrupt-1", [finding.id]
        )
        assert _row(job_store, job.job_id)["attempt"] == 0

        assert job_store.request_resume(job.job_id)
        resume = job_store.claim("worker-1", 60)
        assert resume is not None and resume.kind == "resume"
        assert _row(job_store, job.job_id)["attempt"] == 1
    finally:
        job_store.close()


def test_decide_rejects_job_that_is_already_resuming(tmp_path):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        finding = _finding()
        job = job_store.create("contract.txt")
        job_store.set_result(job.job_id, _report(finding), [])
        job_store._connection().execute(
            "UPDATE jobs SET status = ? WHERE job_id = ?",
            (JobStatus.RESUMING.value, job.job_id),
        )
        with pytest.raises(JobResuming):
            job_store.decide(job.job_id, finding.id, Decision.ACCEPTED, "同意")
    finally:
        job_store.close()


async def test_worker_pool_executes_ready_job():
    job = store.create(
        SAMPLE.name, source_path=str(SAMPLE), cleanup_source=False
    )
    assert store.mark_ready(job.job_id)

    pool = WorkerPool()
    await pool.start()
    try:
        deadline = time.monotonic() + 60
        state = store.get(job.job_id, fresh=True)
        while (
            state.status
            not in {
                JobStatus.COMPLETED,
                JobStatus.COMPLETED_WITH_WARNINGS,
                JobStatus.FAILED,
            }
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.1)
            state = store.get(job.job_id, fresh=True)
    finally:
        await pool.stop()

    assert state.status in {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
    }, state.error
    assert state.result is not None


def test_api_worker_mode_encrypts_state_at_rest():
    old_values = {
        name: getattr(settings, name)
        for name in (
            "worker_enabled",
            "sync_jobs",
            "encryption_enabled",
            "master_key",
        )
    }
    object.__setattr__(settings, "worker_enabled", True)
    object.__setattr__(settings, "sync_jobs", False)
    object.__setattr__(settings, "encryption_enabled", True)
    object.__setattr__(
        settings, "master_key", "contractguard-api-test-master-key-0123456789"
    )
    crypto._derive_key.cache_clear()
    try:
        with TestClient(app) as client:
            response = client.post("/api/reviews/samples/sample_01")
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            deadline = time.monotonic() + 60
            state = client.get(f"/api/reviews/{job_id}").json()
            while (
                state["status"]
                not in {
                    JobStatus.COMPLETED.value,
                    JobStatus.COMPLETED_WITH_WARNINGS.value,
                    JobStatus.FAILED.value,
                }
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
                state = client.get(f"/api/reviews/{job_id}").json()
        assert state["status"] in {
            JobStatus.COMPLETED.value,
            JobStatus.COMPLETED_WITH_WARNINGS.value,
        }, state.get("error")
        row = store._connection().execute(
            "SELECT state_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row is not None
        assert row["state_json"].startswith(STATE_PREFIX)
    finally:
        for name, value in old_values.items():
            object.__setattr__(settings, name, value)
        crypto._derive_key.cache_clear()
