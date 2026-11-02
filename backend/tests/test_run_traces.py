import json

import pytest

from app import crypto
from app.config import settings
from app.schemas import ContractMeta, ReviewResult
from app.services.jobs import _save_trace
from app.services.summary import build_summary
from scripts.export_run_traces import export

MASTER_KEY = "contractguard-test-master-key-0123456789"


@pytest.fixture
def encryption():
    old_enabled = settings.encryption_enabled
    old_key = settings.master_key
    object.__setattr__(settings, "encryption_enabled", True)
    object.__setattr__(settings, "master_key", MASTER_KEY)
    crypto._derive_key.cache_clear()
    try:
        yield
    finally:
        object.__setattr__(settings, "encryption_enabled", old_enabled)
        object.__setattr__(settings, "master_key", old_key)
        crypto._derive_key.cache_clear()


def _report() -> ReviewResult:
    return ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary([]),
        clauses=[],
        findings=[],
        full_text="真实合同全文",
    )


def test_encrypted_trace_contains_no_plaintext(tmp_path, monkeypatch, encryption):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("app.services.jobs.RUNS_DIR", runs_dir)
    _save_trace("job-1", _report())

    raw = (runs_dir / "job-1.json").read_text(encoding="utf-8")
    assert "真实合同全文" not in raw
    envelope = json.loads(raw)
    assert envelope["encrypted"] is True

    out = tmp_path / "out"
    report = export(runs_dir, out, "job-1")
    assert report["failures"] == []
    decrypted = json.loads((out / "job-1.json").read_text(encoding="utf-8"))
    assert decrypted["full_text"] == "真实合同全文"


def test_plaintext_trace_remains_legacy_readable(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("app.services.jobs.RUNS_DIR", runs_dir)
    _save_trace("job-1", _report())
    raw = (runs_dir / "job-1.json").read_text(encoding="utf-8")
    assert json.loads(raw)["full_text"] == "真实合同全文"
    report = export(runs_dir, tmp_path / "out", "job-1")
    assert report["failures"] == []


def test_wrong_key_export_fails_closed(tmp_path, monkeypatch, encryption):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("app.services.jobs.RUNS_DIR", runs_dir)
    _save_trace("job-1", _report())
    object.__setattr__(settings, "master_key", "another-master-key-0123456789")
    crypto._derive_key.cache_clear()

    report = export(runs_dir, tmp_path / "out", "job-1")

    assert report["exported"] == []
    assert report["failures"]
