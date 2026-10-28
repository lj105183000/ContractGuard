"""At-rest encryption: job snapshots and LangGraph checkpoints."""

from __future__ import annotations

import sqlite3

import pytest
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app import crypto
from app.config import settings
from app.crypto import (
    STATE_PREFIX,
    AesGcmCipher,
    CryptoError,
    assert_encryption_ready,
    decrypt_state,
    encrypt_state,
)
from app.schemas import (
    ContractMeta,
    JobStatus,
    ReviewResult,
)
from app.services.jobs import JobStore
from app.services.summary import build_summary

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
        full_text="真实合同全文不应以明文落盘",
    )


def test_state_roundtrip_uses_versioned_prefix(encryption):
    encrypted = encrypt_state("job-1", '{"secret": "甲方联系方式"}')
    assert encrypted.startswith(STATE_PREFIX)
    assert decrypt_state("job-1", encrypted) == '{"secret": "甲方联系方式"}'
    assert "甲方联系方式" not in encrypted


def test_plaintext_job_state_remains_readable(encryption):
    assert decrypt_state("job-1", '{"old": true}') == '{"old": true}'


def test_wrong_key_cannot_decrypt(encryption):
    encrypted = encrypt_state("job-1", '{"a": 1}')
    object.__setattr__(settings, "master_key", "another-master-key-0123456789")
    crypto._derive_key.cache_clear()
    with pytest.raises(CryptoError):
        decrypt_state("job-1", encrypted)


def test_encryption_requires_a_strong_key(encryption):
    object.__setattr__(settings, "master_key", "short")
    crypto._derive_key.cache_clear()
    with pytest.raises(CryptoError):
        assert_encryption_ready()


def test_encrypted_checkpoint_serializer_roundtrip(encryption):
    serde = EncryptedSerializer(AesGcmCipher(), JsonPlusSerializer())
    payload = {"state": "合同全文", "items": [1, 2, 3]}
    wire_type, ciphertext = serde.dumps_typed(payload)
    assert wire_type == "msgpack+aesgcm-v1"
    assert serde.loads_typed((wire_type, ciphertext)) == payload
    # 旧的明文 checkpoint 仍可读取。
    plain = JsonPlusSerializer().dumps_typed(payload)
    assert serde.loads_typed(plain) == payload


def test_job_store_encrypts_state_at_rest(tmp_path, encryption):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    job = first.create("contract.txt")
    first.set_result(job.job_id, _report(), [])
    first.close()

    conn = sqlite3.connect(db_path)
    try:
        state_json = conn.execute(
            "SELECT state_json FROM jobs WHERE job_id = ?", (job.job_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert state_json.startswith(STATE_PREFIX)
    assert "真实合同全文" not in state_json

    second = JobStore(db_path)
    try:
        restored = second.get(job.job_id)
        assert restored.status == JobStatus.COMPLETED
        assert restored.result is not None
        assert restored.result.full_text == "真实合同全文不应以明文落盘"
    finally:
        second.close()


def test_job_store_refuses_to_start_without_key(tmp_path):
    old_enabled = settings.encryption_enabled
    old_key = settings.master_key
    object.__setattr__(settings, "encryption_enabled", True)
    object.__setattr__(settings, "master_key", "")
    crypto._derive_key.cache_clear()
    try:
        with pytest.raises(CryptoError):
            JobStore(tmp_path / "jobs.sqlite3")
    finally:
        object.__setattr__(settings, "encryption_enabled", old_enabled)
        object.__setattr__(settings, "master_key", old_key)
        crypto._derive_key.cache_clear()
