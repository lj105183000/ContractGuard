from urllib.parse import unquote

from app.schemas import (
    Category,
    Clause,
    ContractMeta,
    Finding,
    FindingStatus,
    JobStatus,
    ReviewResult,
    Severity,
    Source,
)
from app.services.jobs import store
from app.services.summary import build_summary


def _upload(client, text: str):
    return client.post(
        "/api/reviews",
        files={"file": ("contract.txt", text.encode("utf-8"), "text/plain")},
    )


def _decide(client, job_id: str, finding_id: str, decision: str):
    return client.post(
        f"/api/reviews/{job_id}/findings/{finding_id}/decision",
        json={"decision": decision, "note": "修订稿测试"},
    )


def test_revised_contract_requires_review_and_blocking_high(client, contract_text):
    job_id = _upload(client, contract_text).json()["job_id"]

    blocked = client.get(f"/api/reviews/{job_id}/revised-contract?format=json")
    assert blocked.status_code == 409
    assert blocked.json()["pending_findings"]

    invalid = client.get(f"/api/reviews/{job_id}/revised-contract?format=pdf")
    assert invalid.status_code == 400

    missing = client.get("/api/reviews/not-a-job/revised-contract")
    assert missing.status_code == 404


def test_revised_contract_exports_annotations_and_appends(client, contract_text):
    job_id = _upload(client, contract_text).json()["job_id"]
    job = client.get(f"/api/reviews/{job_id}").json()
    findings = job["result"]["findings"]
    high = [finding for finding in findings if finding["severity"] == "high"]
    assert len(high) >= 3

    rejected = high[0]
    for finding in high[1:]:
        response = _decide(client, job_id, finding["id"], "accepted")
        assert response.status_code == 200

    rejected = _decide(client, job_id, rejected["id"], "rejected")
    assert rejected.status_code == 200

    contract_level = next(
        (
            finding
            for finding in findings
            if finding["clause_id"] is None and finding["severity"] != "high"
        ),
        None,
    )
    if contract_level:
        assert _decide(client, job_id, contract_level["id"], "accepted").status_code == 200

    payload = client.get(
        f"/api/reviews/{job_id}/revised-contract?format=json"
    )
    assert payload.status_code == 200
    data = payload.json()
    assert data["accepted_count"] >= len(high) - 1
    assert data["replaced_count"] == 0
    assert data["annotated_count"] >= len(high) - 1
    if contract_level:
        assert data["appended_count"] == 1
        assert "补充条款（AI 建议，需人工确认）" in data["text"]
    assert "【修订建议】" in data["text"]
    assert data["changes"]
    assert all(change["change_type"] != "replace" for change in data["changes"])
    assert "合同生效后支付预付款" in data["text"]
    assert rejected.json()["finding"]["id"] not in {
        change["finding_id"] for change in data["changes"]
    }

    markdown = client.get(
        f"/api/reviews/{job_id}/revised-contract?format=md"
    )
    assert markdown.status_code == 200
    assert "text/markdown" in markdown.headers["content-type"]
    assert "合同修订稿" in markdown.content.decode("utf-8")
    assert "filename*=UTF-8''" in markdown.headers["content-disposition"]

    docx = client.get(
        f"/api/reviews/{job_id}/revised-contract?format=docx"
    )
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    assert "wordprocessingml.document" in docx.headers["content-type"]
    disposition = docx.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    encoded = disposition.split("filename*=UTF-8''", 1)[1]
    assert "修订稿" in unquote(encoded)


def test_revised_contract_reflects_later_decision_without_cache(client, contract_text):
    job_id = _upload(client, contract_text).json()["job_id"]
    job = client.get(f"/api/reviews/{job_id}").json()
    findings = job["result"]["findings"]
    high = [finding for finding in findings if finding["severity"] == "high"]
    for finding in high:
        assert _decide(client, job_id, finding["id"], "accepted").status_code == 200

    first = client.get(
        f"/api/reviews/{job_id}/revised-contract?format=json"
    ).json()
    target = high[0]
    assert target["id"] in {change["finding_id"] for change in first["changes"]}

    assert _decide(client, job_id, target["id"], "rejected").status_code == 200
    second = client.get(
        f"/api/reviews/{job_id}/revised-contract?format=json"
    ).json()
    assert target["id"] not in {
        change["finding_id"] for change in second["changes"]
    }


def test_revised_contract_status_gate_and_empty_accepted(client):
    job = store.create("contract.txt")
    report = ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary([]),
        clauses=[],
        findings=[],
        full_text="第一条 测试合同。",
    )
    store.set_result(job.job_id, report, [])
    store._connection().execute(
        "UPDATE jobs SET status = ? WHERE job_id = ?",
        (JobStatus.AWAITING_REVIEW.value, job.job_id),
    )

    waiting = client.get(f"/api/reviews/{job.job_id}/revised-contract")
    assert waiting.status_code == 409
    assert "复核" in waiting.json()["detail"]

    store.set_result(job.job_id, report, [])
    store._connection().execute(
        "UPDATE jobs SET status = 'completed' WHERE job_id = ?", (job.job_id,)
    )
    empty = client.get(f"/api/reviews/{job.job_id}/revised-contract?format=json")
    assert empty.status_code == 200
    data = empty.json()
    assert data["text"] == report.full_text
    assert data["changes"] == []
    assert data["warnings"]


def test_revised_contract_replaces_live_llm_suggestion(client):
    preamble = "前言\n\n"
    clause_text = "第一条 付款\n合同生效后支付 50% 预付款。"
    clause = Clause(
        id="cl_001",
        index=0,
        article="第一条",
        text=clause_text,
        start=len(preamble),
        end=len(preamble) + len(clause_text),
    )
    finding = Finding(
        id="f1",
        clause_id="cl_001",
        clause_article="第一条",
        category=Category.PAYMENT,
        severity=Severity.HIGH,
        source=Source.RULE,
        title="预付款过高",
        suggested_text="合同生效后支付 20% 预付款，验收后支付 80%。",
        suggested_text_source="llm",
        status=FindingStatus.ACCEPTED,
    )
    report = ReviewResult(
        contract=ContractMeta(filename="contract.txt", title="测试合同"),
        summary=build_summary([finding]),
        clauses=[clause],
        findings=[finding],
        full_text=f"前言\n\n{clause_text}\n\n第二条 交付\n30 日内交付。",
        mode="live",
    )
    job = store.create("contract.txt")
    store.set_result(job.job_id, report, [])

    response = client.get(f"/api/reviews/{job.job_id}/revised-contract?format=json")

    assert response.status_code == 200
    data = response.json()
    assert data["replaced_count"] == 1
    assert data["changes"][0]["change_type"] == "replace"
    assert data["changes"][0]["revised_text"].startswith("第一条 付款")
    assert "20% 预付款" in data["text"]
    assert "前言" in data["text"]


def test_revised_contract_without_full_text_returns_409(client):
    report = ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary([]),
        clauses=[],
        findings=[],
        full_text="",
    )
    job = store.create("contract.txt")
    store.set_result(job.job_id, report, [])

    response = client.get(f"/api/reviews/{job.job_id}/revised-contract")

    assert response.status_code == 409
    assert "全文" in response.json()["detail"]
