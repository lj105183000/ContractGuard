from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def _upload_sample(client, filename: str, batch_id: str):
    path = SAMPLES / filename
    with path.open("rb") as handle:
        return client.post(
            "/api/reviews",
            files={"file": (filename, handle, "application/octet-stream")},
            params={"batch_id": batch_id},
        )


def test_batch_upload_view_and_report(client):
    first = _upload_sample(client, "sample_01_数控设备采购合同.docx", "b1")
    second = _upload_sample(client, "sample_02_办公家具采购合同.docx", "b1")
    assert first.status_code == 202
    assert second.status_code == 202
    first_id = first.json()["job_id"]
    second_id = second.json()["job_id"]

    job = client.get(f"/api/reviews/{first_id}").json()
    assert job["batch_id"] == "b1"

    listing = client.get("/api/reviews", params={"batch_id": "b1"}).json()
    assert {item["job_id"] for item in listing} == {first_id, second_id}

    for job_id in (first_id, second_id):
        job = client.get(f"/api/reviews/{job_id}").json()
        for finding in job["result"]["findings"]:
            if (
                finding["severity"] == "high"
                and finding["confidence_tier"] == "confirmed"
                and finding["status"] in {"pending", "needs_human"}
            ):
                decision = client.post(
                    f"/api/reviews/{job_id}/findings/{finding['id']}/decision",
                    json={"decision": "accepted", "note": "batch report test"},
                )
                assert decision.status_code == 200

    batch = client.get("/api/batches/b1")
    assert batch.status_code == 200
    data = batch.json()
    assert data["stats"]["contracts"] == 2
    assert data["export_allowed"] is True
    assert len(data["items"]) == 2
    assert "result" not in data["items"][0]

    preview = client.get("/api/batches/b1/report/preview")
    assert preview.status_code == 200
    assert preview.json()["export_allowed"] is True
    assert "批次审查报告" in preview.json()["markdown"]

    markdown = client.get("/api/batches/b1/report", params={"format": "md"})
    assert markdown.status_code == 200
    text = markdown.content.decode("utf-8")
    assert "批次审查报告" in text
    assert "sample_01" in text and "sample_02" in text

    payload = client.get("/api/batches/b1/report", params={"format": "json"})
    assert payload.status_code == 200
    assert len(payload.json()["contracts"]) == 2

    docx = client.get("/api/batches/b1/report", params={"format": "docx"})
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"


def test_batch_decision_skips_completed_jobs(client):
    response = _upload_sample(
        client, "sample_01_数控设备采购合同.docx", "b2"
    )
    assert response.status_code == 202
    decision = client.post(
        "/api/batches/b2/decisions",
        json={"decision": "accepted", "note": "batch"},
    )
    assert decision.status_code == 200
    data = decision.json()
    assert data["decided"] == 0
    assert data["items"][0]["status"] == "completed"


def test_batch_hitl_decisions_resume_all_jobs(tmp_path, monkeypatch):
    import app.main as main_module
    import app.services.jobs as jobs_module
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
        with TestClient(app) as hitl_client:
            first = _upload_sample(
                hitl_client, "sample_01_数控设备采购合同.docx", "b3"
            )
            second = _upload_sample(
                hitl_client, "sample_02_办公家具采购合同.docx", "b3"
            )
            first_id = first.json()["job_id"]
            second_id = second.json()["job_id"]
            assert hitl_client.get(f"/api/reviews/{first_id}").json()[
                "status"
            ] == "awaiting_review"
            assert hitl_client.get(f"/api/reviews/{second_id}").json()[
                "status"
            ] == "awaiting_review"

            blocked = hitl_client.get(
                "/api/batches/b3/report", params={"format": "md"}
            )
            assert blocked.status_code == 409
            assert len(blocked.json()["blocked_items"]) == 2
            preview = hitl_client.get("/api/batches/b3/report/preview")
            assert preview.status_code == 200
            assert preview.json()["export_allowed"] is False

            response = hitl_client.post(
                "/api/batches/b3/decisions",
                json={"decision": "accepted", "note": "batch hitl"},
            )
            assert response.status_code == 200
            assert response.json()["decided"] > 0

            first_final = hitl_client.get(f"/api/reviews/{first_id}").json()
            second_final = hitl_client.get(f"/api/reviews/{second_id}").json()
            assert first_final["status"] == "completed"
            assert second_final["status"] == "completed"
            batch = hitl_client.get("/api/batches/b3").json()
            assert batch["export_allowed"] is True
            assert hitl_client.get(
                "/api/batches/b3/report", params={"format": "md"}
            ).status_code == 200
    finally:
        store.reset()
