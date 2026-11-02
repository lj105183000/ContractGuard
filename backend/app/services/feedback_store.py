"""Feedback JSONL encoding with optional at-rest encryption.

Encrypted lines use a small JSON envelope so a reader knows the job id without
access to the key; the record itself (notes, finding titles, etc.) stays inside
the AES-GCM ciphertext. Legacy plaintext lines remain readable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from app.config import settings
from app.crypto import CryptoError, decrypt_feedback, encrypt_feedback

FORMAT = "contractguard.feedback"
VERSION = 1


def encode_record(record: dict) -> str:
    plaintext = json.dumps(record, ensure_ascii=False)
    if not settings.encryption_enabled:
        return plaintext
    envelope = {
        "format": FORMAT,
        "version": VERSION,
        "encrypted": True,
        "job_id": record.get("job_id"),
        "ciphertext": encrypt_feedback(plaintext),
    }
    return json.dumps(envelope, ensure_ascii=False)


def decode_line(line: str) -> tuple[dict | None, bool, str | None]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        return None, False, f"malformed: {exc}"
    if not isinstance(data, dict):
        return None, False, "not_object"
    if data.get("encrypted"):
        try:
            plaintext = decrypt_feedback(str(data.get("ciphertext") or ""))
            record = json.loads(plaintext)
        except (CryptoError, json.JSONDecodeError) as exc:
            return None, True, f"decrypt_failed: {exc}"
        if not isinstance(record, dict):
            return None, True, "decrypted_not_object"
        return record, True, None
    return data, False, None


def iter_records(
    path: Path,
) -> Iterator[tuple[str, dict | None, bool, str | None]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record, encrypted, error = decode_line(line)
        yield line, record, encrypted, error
