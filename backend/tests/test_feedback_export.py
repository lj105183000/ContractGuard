import json
import sqlite3

from app import crypto
from app.config import settings
from app.services.feedback_store import encode_record
from scripts.export_feedback import export


def _write_feedback(path):
    records = [
        {
            "schema_version": 2,
            "feedback_id": "fb-1",
            "job_id": "job-1",
            "finding_id": "f-1",
            "decision": "accepted",
            "note": "secret note",
            "mode": "mock",
            "llm_models": ["qwen-plus"],
            "prompt_version": "p1",
            "kb_version": "kb-1",
            "at": "2026-01-01T00:00:00+08:00",
        },
        {
            "job_id": "job-2",
            "finding_id": "f-2",
            "decision": "rejected",
            "note": "legacy note",
            "at": "2026-01-01T00:00:01+08:00",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
        + "\nnot-json\n",
        encoding="utf-8",
    )


def _write_jobs_db(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO jobs (job_id) VALUES ('job-1')")
        conn.commit()
    finally:
        conn.close()


def test_export_feedback_reports_orphans_and_legacy(tmp_path):
    feedback = tmp_path / "feedback.jsonl"
    db = tmp_path / "jobs.sqlite3"
    out = tmp_path / "out"
    _write_feedback(feedback)
    _write_jobs_db(db)

    report = export(
        feedback,
        db,
        out,
        ["jsonl", "csv", "sqlite"],
        include_notes=False,
    )

    assert report["total_lines"] == 3
    assert report["exported_records"] == 2
    assert report["malformed_lines"] == 1
    assert report["legacy_records"] == 1
    assert report["orphan_records"] == 1
    assert report["orphan_rate"] == 0.5
    assert report["notes_included"] is False
    exported = (out / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(exported) == 2
    assert "secret note" not in "\n".join(exported)
    assert (out / "feedback.csv").exists()
    assert (out / "orphans.json").exists()

    conn = sqlite3.connect(out / "feedback.sqlite3")
    try:
        rows = conn.execute(
            "SELECT job_id, llm_models, is_orphan FROM feedback_export ORDER BY job_id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("job-1", '["qwen-plus"]', "0"),
        ("job-2", "[]", "1"),
    ]


def test_export_feedback_can_include_notes(tmp_path):
    feedback = tmp_path / "feedback.jsonl"
    _write_feedback(feedback)
    out = tmp_path / "out"
    report = export(
        feedback,
        tmp_path / "missing.sqlite3",
        out,
        ["jsonl"],
        include_notes=True,
    )
    assert report["db_available"] is False
    assert report["orphan_records"] == 0
    exported = (out / "feedback.jsonl").read_text(encoding="utf-8")
    assert "secret note" in exported


def test_export_decrypts_encrypted_feedback(tmp_path):
    old_enabled = settings.encryption_enabled
    old_key = settings.master_key
    object.__setattr__(settings, "encryption_enabled", True)
    object.__setattr__(settings, "master_key", "contractguard-test-master-key-0123456789")
    crypto._derive_key.cache_clear()
    try:
        feedback = tmp_path / "feedback.jsonl"
        feedback.write_text(
            encode_record(
                {
                    "schema_version": 2,
                    "job_id": "job-1",
                    "finding_id": "f-1",
                    "decision": "accepted",
                    "note": "secret note",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out = tmp_path / "out"
        report = export(
            feedback,
            tmp_path / "missing.sqlite3",
            out,
            ["jsonl"],
            include_notes=False,
        )
        assert report["encrypted_records"] == 1
        assert report["decrypt_errors"] == 0
        raw = (out / "feedback.jsonl").read_text(encoding="utf-8")
        assert "job-1" in raw
        assert "secret note" not in raw
    finally:
        object.__setattr__(settings, "encryption_enabled", old_enabled)
        object.__setattr__(settings, "master_key", old_key)
        crypto._derive_key.cache_clear()
