#!/usr/bin/env python3
"""Check relative links and image paths in repository documentation.

Only local targets are validated; http(s), mailto, data and pure anchors are
skipped. Markdown fenced code blocks and inline code spans are ignored so that
example commands do not produce false positives.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent

TARGETS = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "desktop" / "README.md",
]
TARGETS += sorted((ROOT / "docs").rglob("*.md"))
TARGETS += sorted((ROOT / "docs").rglob("*.html"))
TARGETS += sorted((ROOT / ".github").rglob("*.md"))

SKIP_PREFIXES = (
    "http://",
    "https://",
    "mailto:",
    "tel:",
    "data:",
    "javascript:",
    "ftp://",
    "//",
)

MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*(?:[\"'][^\"']*[\"'])?\s*\)")
MD_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
HTML_ATTR = re.compile(r"(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]+`")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_ignored(text: str, suffix: str) -> str:
    text = FENCED_BLOCK.sub("", text)
    text = INLINE_CODE.sub("", text)
    if suffix == ".html":
        text = HTML_COMMENT.sub("", text)
    return text


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_local(target: str) -> bool:
    target = target.strip()
    if not target or target.startswith("#"):
        return False
    if target.startswith(SKIP_PREFIXES):
        return False
    if target.startswith("{") or target.startswith("$"):
        return False
    return True


def local_path(target: str) -> str:
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target)


def extract_links(text: str, suffix: str) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    for pattern in (MD_LINK, MD_REF_DEF) if suffix == ".md" else ():
        for match in pattern.finditer(text):
            raw = match.group(1) if pattern is MD_LINK else match.group(1)
            if raw:
                links.append((line_number(text, match.start()), raw))
    for match in HTML_ATTR.finditer(text):
        raw = match.group(1) or match.group(2) or ""
        links.append((line_number(text, match.start()), raw))
    return links


def main() -> int:
    files = [path for path in TARGETS if path.is_file()]
    missing: list[str] = []
    checked = 0

    for path in files:
        text = path.read_text(encoding="utf-8")
        text = strip_ignored(text, path.suffix)
        for line, target in extract_links(text, path.suffix):
            if not is_local(target):
                continue
            rel = local_path(target)
            if not rel:
                continue
            candidate = (path.parent / rel).resolve()
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                missing.append(f"{path.relative_to(ROOT)}:{line}: escapes repository: {target}")
                continue
            checked += 1
            if not candidate.exists():
                missing.append(f"{path.relative_to(ROOT)}:{line}: missing target: {target}")

    if missing:
        print("Broken local links:")
        for item in missing:
            print(f"  - {item}")
        print(f"\n{len(missing)} broken link(s) in {len(files)} files.")
        return 1

    print(f"OK: {checked} local links checked across {len(files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
