"""Lightweight PII detection/redaction helpers.

Detection is intentionally conservative and value-agnostic: callers get counts
and can redact without persisting the matched values. This is not a legal
anonymization guarantee; real-contract egress policy must be reviewed by the
data owner.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "bank_account": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    "company": re.compile(
        r"[一-龥]{2,30}(?:有限责任公司|股份有限公司|有限公司)"
    ),
}

REDACTIONS = {
    "email": "<EMAIL>",
    "phone": "<PHONE>",
    "id_card": "<ID_CARD>",
    "bank_account": "<BANK_ACCOUNT>",
    "company": "<COMPANY>",
}


def scan_text(text: str) -> dict[str, int]:
    spans = {
        name: {match.span() for match in pattern.finditer(text or "")}
        for name, pattern in PATTERNS.items()
    }
    # An 18-digit ID card also matches the generic bank-account pattern; count
    # it once, as ID card, to avoid inflating both totals.
    spans["bank_account"] -= spans["id_card"]
    return {name: len(values) for name, values in spans.items()}


def redact_text(text: str) -> str:
    redacted = text or ""
    # Longer/overlapping identifiers first: ID card before bank account.
    for name in ("email", "id_card", "bank_account", "phone", "company"):
        redacted = PATTERNS[name].sub(REDACTIONS[name], redacted)
    return redacted
