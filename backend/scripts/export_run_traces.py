"""Decrypt/export run traces for audit.

Exported traces contain full contract text. Keep the output directory private
and never commit it.

Usage (backend directory):
    uv run python scripts/export_run_traces.py --out-dir ../exports/traces
    uv run python scripts/export_run_traces.py --out-dir /tmp/traces --job-id <id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import RUNS_DIR, settings  # noqa: E402
from app.crypto import CryptoError, decrypt_trace  # noqa: E402


def export(runs_dir: Path, out_dir: Path, job_id: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = (
        [runs_dir / f"{job_id}.json"]
        if job_id
        else sorted(runs_dir.glob("*.json"))
    )
    exported = []
    failures = []
    for path in files:
        if not path.exists():
            failures.append({"file": path.name, "error": "missing"})
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("encrypted"):
                trace_id = str(data.get("job_id") or path.stem)
                plaintext = decrypt_trace(trace_id, str(data.get("ciphertext") or ""))
                trace = json.loads(plaintext)
            else:
                trace = data
            target = out_dir / path.name
            target.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            exported.append(path.name)
        except (OSError, json.JSONDecodeError, CryptoError) as exc:
            failures.append({"file": path.name, "error": str(exc)})
    return {
        "runs_dir": str(runs_dir),
        "out_dir": str(out_dir),
        "encryption_enabled": settings.encryption_enabled,
        "exported": exported,
        "failures": failures,
        "warning": "导出文件包含合同全文；请勿提交或公开。",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args()
    report = export(Path(args.runs_dir), Path(args.out_dir), args.job_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
