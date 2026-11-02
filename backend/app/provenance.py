"""Content provenance helpers shared by runtime and evaluation code.

These helpers are intentionally dependency-free and never persist secrets or
absolute paths. They provide stable hashes/versions for reproducible eval and
feedback records.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1
APP_VERSION = "0.1.0"

BACKEND = Path(__file__).resolve().parents[1]
PROMPTS_PATH = BACKEND / "app" / "llm" / "prompts.py"
PROFILES_PATH = BACKEND / "data" / "llm_profiles.yaml"

_REDACT_FLAGS = {
    "--contracts-dir",
    "--manifest",
    "--labels-dir",
    "--raw-dir",
    "--scores",
    "--labels-a",
    "--labels-b",
    "--universe",
    "--parsed",
    "--pdf",
    "--out-dir",
    "--out",
    "--output",
    "--output-dir",
    "--backup-dir",
    "--feedback",
    "--db",
    "--runs-dir",
    "--input",
}

_ABSOLUTE_PATH = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|/|\.\.(?:[\\/]|$))"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(Path(path).read_bytes())
    except OSError:
        return None


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def aggregate_sha256(entries: Iterable[tuple[str, str | None]]) -> str:
    normalized = sorted(
        [key, value or ""] for key, value in entries
    )
    return sha256_text(canonical_json(normalized))


def git_provenance(repo_root: Path | None = None) -> dict[str, Any]:
    """Return commit/dirty state without paths, remotes or user identity."""
    root = str(repo_root or BACKEND)
    try:
        commit = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if commit.returncode != 0:
            return {"commit": None, "dirty": None, "error": "git_unavailable"}
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return {
            "commit": commit.stdout.strip() or None,
            "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        }
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None, "error": "git_unavailable"}


def prompt_provenance() -> dict[str, Any]:
    from app.llm.prompts import PROMPT_VERSION

    return {
        "version": PROMPT_VERSION,
        "file_sha256": sha256_file(PROMPTS_PATH),
        "source": "app/llm/prompts.py",
    }


def profiles_provenance() -> dict[str, Any]:
    digest = sha256_file(PROFILES_PATH)
    return {
        "version": f"profiles-{(digest or 'missing')[:12]}",
        "sha256": digest,
        "source": "data/llm_profiles.yaml",
    }


@lru_cache(maxsize=1)
def kb_provenance() -> dict[str, Any]:
    """Content-addressed KB version; lazy import avoids app import cycles."""
    from app.rag.corpus import load_kb

    kb = load_kb()
    legal = sorted(
        (article.id, article.model_dump(mode="json"))
        for article in kb.legal_articles
    )
    rules = sorted(
        (rule.id, rule.model_dump(mode="json")) for rule in kb.rules
    )
    digest = sha256_text(canonical_json({"legal": legal, "rules": rules}))
    return {
        "version": f"kb-{digest[:12]}",
        "sha256": digest,
        "legal_articles": len(kb.legal_articles),
        "rules": len(kb.rules),
    }


def contract_sha256(text: str | None) -> str | None:
    if not text:
        return None
    return sha256_text(text)


def python_version() -> str:
    return platform.python_version()


def redact_argv(argv: Sequence[str]) -> tuple[list[str], int]:
    """Redact private paths from a command line while keeping flags visible."""
    redacted: list[str] = []
    count = 0
    entrypoint = Path(argv[0]).as_posix() if argv else ""
    if _ABSOLUTE_PATH.match(entrypoint):
        entrypoint = Path(entrypoint).name
    redacted.append(entrypoint)
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in _REDACT_FLAGS and index + 1 < len(argv):
            redacted.extend([token, "<redacted>"])
            count += 1
            index += 2
            continue
        if "=" in token and token.split("=", 1)[0] in _REDACT_FLAGS:
            redacted.append(f"{token.split('=', 1)[0]}=<redacted>")
            count += 1
            index += 1
            continue
        if _ABSOLUTE_PATH.match(token):
            redacted.append("<redacted>")
            count += 1
            index += 1
            continue
        redacted.append(token)
        index += 1
    return redacted, count


def command_provenance(entrypoint: str, argv: Sequence[str]) -> dict[str, Any]:
    redacted, count = redact_argv([entrypoint, *argv])
    return {
        "entrypoint": Path(entrypoint).as_posix(),
        "argv": redacted[1:],
        "redacted_paths": count,
    }
