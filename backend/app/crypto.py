"""At-rest encryption for job state and LangGraph checkpoints.

Opt-in via ENCRYPTION_ENABLED=1 + CONTRACTGUARD_MASTER_KEY. The master key is
hashed with SHA-256 and expanded with HKDF into separate job-state and
checkpoint keys. Old plaintext job-state/checkpoint values remain readable.
"""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

STATE_PREFIX = "enc:v1:"
CHECKPOINT_CIPHER = "aesgcm-v1"


class CryptoError(Exception):
    pass


@lru_cache(maxsize=8)
def _derive_key(info: str) -> bytes:
    if not settings.master_key:
        raise CryptoError(
            "ENCRYPTION_ENABLED=1 但未配置 CONTRACTGUARD_MASTER_KEY"
        )
    if len(settings.master_key) < 16:
        raise CryptoError(
            "CONTRACTGUARD_MASTER_KEY 太短；建议至少 32 字节随机值"
        )
    material = hashlib.sha256(settings.master_key.encode("utf-8")).digest()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"contractguard",
        info=info.encode("utf-8"),
    ).derive(material)


def assert_encryption_ready() -> None:
    """启动时快速失败：ENCRYPTION_ENABLED=1 必须有可用主密钥。"""
    if not settings.encryption_enabled:
        return
    _derive_key("job-state-v1")
    _derive_key("checkpoint-v1")


def encrypt_state(job_id: str, plaintext: str) -> str:
    if not settings.encryption_enabled:
        return plaintext
    key = _derive_key("job-state-v1")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(
        nonce, plaintext.encode("utf-8"), f"job:{job_id}:v1".encode("utf-8")
    )
    return STATE_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode(
        "ascii"
    )


def decrypt_state(job_id: str, value: str) -> str:
    if not value.startswith(STATE_PREFIX):
        return value
    key = _derive_key("job-state-v1")
    try:
        payload = base64.urlsafe_b64decode(value[len(STATE_PREFIX) :])
        nonce, ciphertext = payload[:12], payload[12:]
        plaintext = AESGCM(key).decrypt(
            nonce, ciphertext, f"job:{job_id}:v1".encode("utf-8")
        )
        return plaintext.decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise CryptoError(f"任务快照解密失败：{exc}") from exc


class AesGcmCipher:
    """LangGraph EncryptedSerializer cipher with a versioned ciphername."""

    def __init__(self) -> None:
        self.key = _derive_key("checkpoint-v1")

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(
            nonce, plaintext, b"contractguard-checkpoint-v1"
        )
        return CHECKPOINT_CIPHER, nonce + ciphertext

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        if ciphername != CHECKPOINT_CIPHER:
            raise CryptoError(f"不支持的 checkpoint cipher：{ciphername}")
        try:
            nonce, payload = ciphertext[:12], ciphertext[12:]
            return AESGCM(self.key).decrypt(
                nonce, payload, b"contractguard-checkpoint-v1"
            )
        except Exception as exc:  # noqa: BLE001
            raise CryptoError(f"checkpoint 解密失败：{exc}") from exc
