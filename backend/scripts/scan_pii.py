"""Scan contract/text files for PII counts without printing matched values.

Usage (backend directory):
    uv run python scripts/scan_pii.py --path ../contractguard-private/docs
    uv run python scripts/scan_pii.py --path contract.txt --json

This is a detection aid, not an anonymization guarantee. Live mode still sends
contract text to external model providers unless the operator redacts or uses a
local provider.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsing.contract_parser import ALLOWED_EXTENSIONS, parse_contract  # noqa: E402
from app.pii import scan_text  # noqa: E402

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def _text_for(path: Path) -> str:
    if path.suffix.lower() in ALLOWED_EXTENSIONS:
        return parse_contract(path, path.name).text
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    target = Path(args.path).expanduser()
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = [
            path
            for path in sorted(target.rglob("*"))
            if path.is_file()
            and not any(part in SKIP_DIRS for part in path.parts)
            and path.suffix.lower() in ALLOWED_EXTENSIONS | {".txt", ".md", ".json", ".yaml", ".yml"}
        ]
    else:
        raise SystemExit(f"path not found: {target}")

    rows = []
    totals = {}
    failures = []
    for path in files:
        try:
            counts = scan_text(_text_for(path))
        except Exception as exc:  # noqa: BLE001
            failures.append({"file": path.name, "error": str(exc)})
            continue
        rows.append({"file": path.name, "counts": counts})
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value

    report = {
        "files": len(rows),
        "totals": totals,
        "rows": rows,
        "failures": failures,
        "note": "只输出计数，不输出匹配到的原始值；检测不是匿名化保证。",
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"扫描 {report['files']} 个文件；PII 计数：{totals}")
        for row in rows:
            if any(row["counts"].values()):
                print(f"  {row['file']}: {row['counts']}")
        for failure in failures:
            print(f"  ERROR {failure['file']}: {failure['error']}")


if __name__ == "__main__":
    main()
