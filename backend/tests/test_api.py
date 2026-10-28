from dataclasses import replace

from app.api import routes
from app.config import UPLOAD_DIR, settings
from app.parsing import ocr
from app.services.jobs import store

OCR_TEXT = (
    "第一条 合同标的\n"
    "甲方向乙方采购数控设备一套，合同总价为人民币100万元，"
    "验收合格后30日内支付尾款。"
)


def _upload(client, contract_text, filename="contract.txt", params=None):
    return client.post(
        "/api/reviews",
        files={"file": (filename, contract_text.encode("utf-8"), "text/plain")},
        params=params,
    )


def test_meta_and_samples(client):
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    data = meta.json()
    assert data["mode"] == "mock"
    assert data["legal_count"] == 32
    assert data["rule_count"] == 25
    assert data["sample_count"] == 8
    assert data["max_upload_mb"] == settings.max_upload_mb
    assert data["ocr_max_pages"] == settings.ocr_max_pages
    assert data["llm_route"]
    assert isinstance(data["llm_routes"], list) and data["llm_routes"]
    assert all("id" in route and "models" in route for route in data["llm_routes"])
    assert "api_key" not in meta.text
    assert "base_url" not in meta.text
    samples = client.get("/api/samples")
    assert samples.status_code == 200
    assert len(samples.json()) == 8


def test_health_reports_readiness_checks(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["db"] == "ok"
    assert "queued" in data["checks"]


def test_metrics_endpoint(client):
    response = client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "requests" in data
    assert "queue" in data
    assert "worker" in data


def test_optional_api_token(client):
    from app.api.limits import reset_rate_limits

    old = settings.api_token
    object.__setattr__(settings, "api_token", "test-secret")
    reset_rate_limits()
    try:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/meta").status_code == 401
        authorized = client.get(
            "/api/meta", headers={"Authorization": "Bearer test-secret"}
        )
        assert authorized.status_code == 200
    finally:
        object.__setattr__(settings, "api_token", old)


def test_rate_limit_and_active_job_cap(client):
    from app.api.limits import reset_rate_limits

    old_rate = settings.rate_limit_per_minute
    old_active = settings.max_active_jobs
    object.__setattr__(settings, "rate_limit_per_minute", 1)
    reset_rate_limits()
    try:
        assert client.post("/api/reviews/samples/sample_01").status_code == 202
        assert client.post("/api/reviews/samples/sample_01").status_code == 429
    finally:
        object.__setattr__(settings, "rate_limit_per_minute", old_rate)
        reset_rate_limits()

    object.__setattr__(settings, "max_active_jobs", 1)
    try:
        job = store.create("queued.txt")
        store.mark_ready(job.job_id)
        assert client.post("/api/reviews/samples/sample_01").status_code == 429
    finally:
        object.__setattr__(settings, "max_active_jobs", old_active)
        store.discard(job.job_id)


def test_update_suggestion_marks_human_edited(client, contract_text):
    job_id = _upload(client, contract_text).json()["job_id"]
    job = client.get(f"/api/reviews/{job_id}").json()
    finding = job["result"]["findings"][0]

    response = client.patch(
        f"/api/reviews/{job_id}/findings/{finding['id']}/suggestion",
        json={"suggested_text": "第三条 付款方式\n预付款调整为 20%。"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["suggested_text_source"] == "human"
    assert "20%" in updated["suggested_text"]
    restored = client.get(f"/api/reviews/{job_id}").json()
    persisted = next(
        item for item in restored["result"]["findings"] if item["id"] == finding["id"]
    )
    assert persisted["suggested_text_source"] == "human"


def test_cancel_and_retry_api(client, contract_text, tmp_path):
    source = tmp_path / "contract.txt"
    source.write_text(contract_text, encoding="utf-8")
    job = store.create(
        "contract.txt", source_path=str(source), cleanup_source=False
    )
    store.mark_ready(job.job_id)

    cancelled = client.post(f"/api/reviews/{job.job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert store.get(job.job_id, fresh=True).status.value == "cancelled"

    retried = client.post(f"/api/reviews/{job.job_id}/retry")
    assert retried.status_code == 202
    final = client.get(f"/api/reviews/{job.job_id}").json()
    assert final["status"] in {"completed", "completed_with_warnings"}


def test_retry_without_source_returns_409(client):
    job = store.create("contract.txt")
    store.set_failed(job.job_id, "boom")
    response = client.post(f"/api/reviews/{job.job_id}/retry")
    assert response.status_code == 409


def test_upload_signature_mismatch_is_rejected(client):
    response = client.post(
        "/api/reviews",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400
    assert "不匹配" in response.json()["detail"]


def test_review_with_ip_clause_completes(client):
    text = """设备采购合同

甲方：星海智能装备有限公司
乙方：远航机电有限公司

第一条 合同标的
甲方向乙方采购数控设备一套。

第二条 合同价款
合同总价为人民币100万元（含税）。

第八条 知识产权
知识产权归甲方所有，乙方不得主张。

第九条 争议解决
因本合同发生争议，由甲方所在地人民法院管辖。
"""
    response = _upload(client, text)
    assert response.status_code == 202
    job = client.get(f"/api/reviews/{response.json()['job_id']}").json()
    assert job["status"] in {"completed", "completed_with_warnings"}
    assert "no such group" not in (job.get("error") or "")


def test_route_selection_and_unknown_route(client, contract_text):
    meta = client.get("/api/meta").json()
    route_id = meta["llm_route"]

    response = _upload(client, contract_text, params={"llm_route": route_id})
    assert response.status_code == 202
    job = client.get(f"/api/reviews/{response.json()['job_id']}").json()
    assert job["llm_route"] == route_id
    assert job["result"]["usage"]["llm_route"] == route_id

    invalid = _upload(client, contract_text, params={"llm_route": "missing-route"})
    assert invalid.status_code == 400
    assert "模型路由不存在" in invalid.json()["detail"]


def test_live_unavailable_route_rejected(
    client, monkeypatch, tmp_path, contract_text
):
    from app.llm import profiles

    profiles.reset_registry()
    monkeypatch.setenv("TEST_LLM_KEY", "test-key")
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """
default_route: ok
providers:
  ok:
    base_url: https://ok.example/v1
    model: ok-model
    api_key_env: TEST_LLM_KEY
  unavailable:
    base_url: https://bad.example/v1
    model: bad-model
    api_key_env: MISSING_LLM_KEY
routes:
  ok:
    default: [ok]
  unavailable:
    default: [unavailable]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        routes,
        "settings",
        replace(settings, llm_mode="live", llm_profiles_path=str(path)),
    )

    response = _upload(client, contract_text, params={"llm_route": "unavailable"})

    assert response.status_code == 400
    assert "模型路由不可用" in response.json()["detail"]
    profiles.reset_registry()


def test_upload_over_limit_cleans_up(client, monkeypatch):
    fixed = store.create("too-big.txt")
    monkeypatch.setattr(
        store,
        "create",
        lambda filename, llm_route=None, batch_id=None: fixed,
    )
    monkeypatch.setattr(routes, "settings", replace(settings, max_upload_mb=1))

    response = client.post(
        "/api/reviews",
        files={"file": ("too-big.txt", b"x" * (2 * 1024 * 1024), "text/plain")},
    )

    assert response.status_code == 413
    assert "文件超过 1MB" in response.json()["detail"]
    assert not (UPLOAD_DIR / fixed.job_id).exists()
    assert client.get(f"/api/reviews/{fixed.job_id}").status_code == 404


def test_review_decisions_and_exports(client, contract_text):
    response = _upload(client, contract_text)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    job = client.get(f"/api/reviews/{job_id}")
    assert job.status_code == 200
    data = job.json()
    assert data["status"] == "completed"
    findings = data["result"]["findings"]
    assert len(findings) >= 5
    assert data["result"]["summary"]["high"] >= 3

    blocked = client.get(f"/api/reviews/{job_id}/report?format=md")
    assert blocked.status_code == 409
    assert blocked.json()["pending_findings"]

    for finding in findings:
        if finding["severity"] == "high":
            decision = client.post(
                f"/api/reviews/{job_id}/findings/{finding['id']}/decision",
                json={"decision": "accepted", "note": "测试采纳"},
            )
            assert decision.status_code == 200
            assert decision.json()["finding"]["status"] == "accepted"

    updated = client.get(f"/api/reviews/{job_id}").json()
    assert updated["result"]["summary"]["high_pending"] == 0

    markdown = client.get(f"/api/reviews/{job_id}/report?format=md")
    assert markdown.status_code == 200
    assert "合同审查报告" in markdown.content.decode("utf-8")
    assert "text/markdown" in markdown.headers["content-type"]

    docx = client.get(f"/api/reviews/{job_id}/report?format=docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    assert len(docx.content) > 10_000

    payload = client.get(f"/api/reviews/{job_id}/report?format=json")
    assert payload.status_code == 200
    assert "findings" in payload.json()


def test_sample_review(client):
    response = client.post("/api/reviews/samples/sample_01")
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = client.get(f"/api/reviews/{job_id}").json()
    assert job["status"] == "completed"
    assert job["result"]["contract"]["filename"].startswith("sample_01")
    assert len(job["result"]["findings"]) >= 5


def test_image_upload_uses_ocr(client, scan_image, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_image", lambda image: (OCR_TEXT, 0.9))

    with scan_image.open("rb") as handle:
        response = client.post(
            "/api/reviews",
            files={"file": ("scan.png", handle, "image/png")},
        )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = client.get(f"/api/reviews/{job_id}").json()
    assert job["status"] == "completed_with_warnings"
    assert job["result"]["contract"]["source_type"] == "image"
    assert any("OCR" in warning for warning in job["warnings"])


def test_invalid_upload_and_missing_job(client):
    bad = client.post(
        "/api/reviews",
        files={"file": ("contract.exe", b"not a contract", "application/octet-stream")},
    )
    assert bad.status_code == 400
    assert client.get("/api/reviews/not-found").status_code == 404


def test_full_text_and_report_preview_gate(client, contract_text):
    response = _upload(client, contract_text)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = client.get(f"/api/reviews/{job_id}").json()
    result = job["result"]

    assert result["full_text"].strip() == contract_text.strip()
    for clause in result["clauses"]:
        assert result["full_text"][clause["start"] : clause["end"]] == clause["text"]

    blocking = [
        finding
        for finding in result["findings"]
        if finding["severity"] == "high"
        and finding["confidence_tier"] == "confirmed"
        and finding["status"] in {"pending", "needs_human"}
    ]
    assert blocking

    preview = client.get(f"/api/reviews/{job_id}/report/preview")
    assert preview.status_code == 200
    data = preview.json()
    assert data["pending_high"] == len(blocking)
    assert data["export_allowed"] is False
    assert "合同审查报告" in data["markdown"]
    assert client.get(f"/api/reviews/{job_id}/report?format=md").status_code == 409

    for finding in blocking:
        decision = client.post(
            f"/api/reviews/{job_id}/findings/{finding['id']}/decision",
            json={"decision": "accepted", "note": "预览门禁测试"},
        )
        assert decision.status_code == 200

    preview = client.get(f"/api/reviews/{job_id}/report/preview").json()
    assert preview["pending_high"] == 0
    assert preview["export_allowed"] is True
    assert client.get(f"/api/reviews/{job_id}/report?format=md").status_code == 200


def test_list_reviews(client, contract_text):
    response = _upload(client, contract_text)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    listing = client.get("/api/reviews")
    assert listing.status_code == 200
    items = listing.json()
    item = next(entry for entry in items if entry["job_id"] == job_id)
    assert item["filename"] == "contract.txt"
    assert item["status"] in {"completed", "completed_with_warnings"}
    assert "result" not in item
    assert "full_text" not in item

    completed = client.get("/api/reviews", params={"status": "completed"})
    assert completed.status_code == 200
    assert all(entry["status"] == "completed" for entry in completed.json())
