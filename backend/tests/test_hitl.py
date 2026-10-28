from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph.build import build_graph
from app.llm.trace import TraceCollector
from app.schemas import (
    Category,
    ConfidenceTier,
    ContractMeta,
    Finding,
    FindingStatus,
    JobStatus,
    ReviewResult,
    Severity,
    Source,
)
from app.services.jobs import JobStore
from app.services.summary import blocking_high_findings, build_summary

SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "samples"
    / "sample_01_数控设备采购合同.docx"
)


async def test_graph_pauses_and_resumes_with_decisions():
    saver = InMemorySaver()
    graph = build_graph(checkpointer=saver, enable_hitl=True)
    config = {
        "configurable": {"thread_id": "hitl-graph-test"},
        "recursion_limit": 30,
    }
    state = {
        "job_id": "hitl-graph-test",
        "filename": SAMPLE.name,
        "source_path": str(SAMPLE),
        "llm_mode": "mock",
        "trace": TraceCollector(),
    }

    first = await graph.ainvoke(state, config=config, durability="sync")
    assert "__interrupt__" in first
    report = first["report"]
    blocking = blocking_high_findings(report.findings)
    assert len(blocking) >= 1

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("human_review",)
    assert snapshot.interrupts

    decisions = {
        finding.id: {"decision": "accepted", "note": "测试采纳"}
        for finding in blocking
    }
    command = Command(
        resume={snapshot.interrupts[0].id: {"decisions": decisions}}
    )
    second = await graph.ainvoke(command, config=config, durability="sync")
    assert "__interrupt__" not in second
    final_report = second["report"]
    applied = {
        finding.id: finding.status
        for finding in final_report.findings
        if finding.id in decisions
    }
    assert applied
    assert all(status == FindingStatus.ACCEPTED for status in applied.values())
    assert not blocking_high_findings(final_report.findings)


def _report(finding: Finding) -> ReviewResult:
    return ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary([finding]),
        clauses=[],
        findings=[finding],
    )


def _finding(status: FindingStatus = FindingStatus.PENDING) -> Finding:
    return Finding(
        id="f1",
        category=Category.PAYMENT,
        severity=Severity.HIGH,
        source=Source.RULE,
        title="预付款比例过高",
        confidence_tier=ConfidenceTier.CONFIRMED,
        status=status,
    )


def test_job_store_awaiting_review_and_resume_cas(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = store.create("contract.txt", cleanup_source=False)
        report = _report(_finding())
        store.set_awaiting_review(
            job.job_id, report, [], "interrupt-1", ["f1"]
        )
        current = store.get(job.job_id)
        assert current.status == JobStatus.AWAITING_REVIEW
        assert current.interrupt_id == "interrupt-1"
        assert current.pending_review_ids == ["f1"]

        assert store.begin_resume(job.job_id) is True
        assert store.begin_resume(job.job_id) is False
        assert store.get(job.job_id).status == JobStatus.RESUMING

        store.set_result(job.job_id, report, [])
        completed = store.get(job.job_id)
        assert completed.status == JobStatus.COMPLETED
        assert completed.interrupt_id is None
        assert completed.pending_review_ids == []
    finally:
        store.close()


def test_awaiting_resume_jobs_only_returns_resolved_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        pending_job = store.create("pending.txt", cleanup_source=False)
        store.set_awaiting_review(
            pending_job.job_id,
            _report(_finding()),
            [],
            "interrupt-pending",
            ["f1"],
        )
        resolved_job = store.create("resolved.txt", cleanup_source=False)
        resolved_report = _report(_finding(FindingStatus.ACCEPTED))
        resolved_report.summary = build_summary(resolved_report.findings)
        store.set_awaiting_review(
            resolved_job.job_id,
            resolved_report,
            [],
            "interrupt-resolved",
            [],
        )

        candidates = {job.job_id for job in store.awaiting_resume_jobs()}
        assert resolved_job.job_id in candidates
        assert pending_job.job_id not in candidates
    finally:
        store.close()


def test_recover_incomplete_keeps_paused_and_resets_resuming(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    paused = first.create("paused.txt", cleanup_source=False)
    first.set_awaiting_review(
        paused.job_id, _report(_finding()), [], "interrupt-paused", ["f1"]
    )
    resuming = first.create("resuming.txt", cleanup_source=False)
    first.set_awaiting_review(
        resuming.job_id,
        _report(_finding(FindingStatus.ACCEPTED)),
        [],
        "interrupt-resuming",
        [],
    )
    first.begin_resume(resuming.job_id)
    running = first.create("running.txt", cleanup_source=False)
    first.set_running(running.job_id)
    first.close()

    second = JobStore(db_path)
    try:
        assert second.get(paused.job_id).status == JobStatus.AWAITING_REVIEW
        assert second.get(resuming.job_id).status == JobStatus.AWAITING_REVIEW
        assert second.get(running.job_id).status == JobStatus.FAILED
    finally:
        second.close()


def test_hitl_api_flow_with_temporary_checkpointer(tmp_path, monkeypatch):
    from dataclasses import replace

    import app.main as main_module
    import app.services.jobs as jobs_module
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.services.jobs import store

    patched = replace(
        settings,
        hitl_enabled=True,
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite3"),
        hitl_resume_on_startup=False,
    )
    monkeypatch.setattr(main_module, "settings", patched)
    monkeypatch.setattr(jobs_module, "settings", patched)

    try:
        with TestClient(app) as client:
            created = client.post("/api/reviews/samples/sample_01")
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            job = client.get(f"/api/reviews/{job_id}").json()
            assert job["status"] == "awaiting_review"
            blocking = [
                finding
                for finding in job["result"]["findings"]
                if finding["severity"] == "high"
                and finding["confidence_tier"] == "confirmed"
                and finding["status"] in {"pending", "needs_human"}
            ]
            assert blocking
            assert client.get(
                f"/api/reviews/{job_id}/report?format=md"
            ).status_code == 409
            for finding in blocking:
                response = client.post(
                    f"/api/reviews/{job_id}/findings/{finding['id']}/decision",
                    json={"decision": "accepted", "note": "api hitl"},
                )
                assert response.status_code == 200
            final = client.get(f"/api/reviews/{job_id}").json()
            assert final["status"] == "completed"
            assert final["result"]["summary"]["blocking_high_pending"] == 0
            assert client.get(
                f"/api/reviews/{job_id}/report?format=md"
            ).status_code == 200
    finally:
        store.reset()
