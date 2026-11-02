import json

import pytest

from app import crypto
from app.config import settings
from app.services.feedback_store import decode_line, encode_record

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


def test_plaintext_feedback_roundtrip():
    record = {"job_id": "job-1", "decision": "accepted"}
    line = encode_record(record)
    assert json.loads(line) == record
    decoded, encrypted, error = decode_line(line)
    assert decoded == record
    assert encrypted is False
    assert error is None


def test_encrypted_feedback_envelope_roundtrip(encryption):
    record = {"job_id": "job-1", "note": "联系人张三"}
    line = encode_record(record)
    assert "联系人张三" not in line
    envelope = json.loads(line)
    assert envelope["encrypted"] is True
    assert envelope["job_id"] == "job-1"
    decoded, encrypted, error = decode_line(line)
    assert decoded == record
    assert encrypted is True
    assert error is None


def test_legacy_plaintext_line_survives_encryption(encryption):
    decoded, encrypted, error = decode_line(
        '{"job_id": "legacy", "decision": "rejected"}'
    )
    assert decoded["job_id"] == "legacy"
    assert encrypted is False
    assert error is None


def test_malformed_or_wrong_key_is_reported(encryption):
    decoded, encrypted, error = decode_line("not-json")
    assert decoded is None and encrypted is False and error.startswith("malformed")

    line = encode_record({"job_id": "job-1", "note": "secret"})
    object.__setattr__(settings, "master_key", "another-master-key-0123456789")
    crypto._derive_key.cache_clear()
    decoded, encrypted, error = decode_line(line)
    assert decoded is None
    assert encrypted is True
    assert error.startswith("decrypt_failed")
