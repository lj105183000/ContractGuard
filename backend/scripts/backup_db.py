"""Consistent SQLite backup for jobs/checkpoints, optionally uploads.

Usage (backend directory):
    uv run python scripts/backup_db.py --output-dir backups
    uv run python scripts/backup_db.py --output-dir backups --include-uploads
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import FEEDBACK_DIR, RUNS_DIR, UPLOAD_DIR, settings  # noqa: E402
from app.provenance import sha256_file  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


def backup_sqlite(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(BACKEND / "backups"))
    parser.add_argument("--include-uploads", action="store_true")
    parser.add_argument("--include-feedback", action="store_true")
    parser.add_argument("--include-traces", action="store_true")
    parser.add_argument("--include-eval", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path(args.output_dir).expanduser() / stamp
    target.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": stamp,
        "files": [],
        "uploads": False,
        "feedback": False,
        "traces": False,
        "eval_files": [],
        "encryption_enabled": settings.encryption_enabled,
        "master_key_required": bool(settings.encryption_enabled),
    }
    for name, source in (
        ("jobs.sqlite3", Path(settings.job_db_path).expanduser()),
        ("checkpoints.sqlite3", Path(settings.checkpoint_db_path).expanduser()),
    ):
        if backup_sqlite(source, target / name):
            manifest["files"].append(name)
    if args.include_uploads and UPLOAD_DIR.exists():
        shutil.copytree(UPLOAD_DIR, target / "uploads", dirs_exist_ok=True)
        manifest["uploads"] = True
    if args.include_feedback:
        feedback = FEEDBACK_DIR / "feedback.jsonl"
        if feedback.exists():
            destination = target / "feedback" / "feedback.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(feedback, destination)
            manifest["feedback"] = True
            manifest["feedback_sha256"] = sha256_file(feedback)
    if args.include_traces and RUNS_DIR.exists():
        shutil.copytree(RUNS_DIR, target / "runs", dirs_exist_ok=True)
        manifest["traces"] = True
    if args.include_eval:
        eval_dir = BACKEND / "eval"
        (target / "eval").mkdir(parents=True, exist_ok=True)
        for name in (
            "badcases.yaml",
            "results_rules.json",
            "report_rules.md",
            "results_full.json",
            "report_full.md",
            "results_retrieval.json",
            "report_retrieval.md",
        ):
            source = eval_dir / name
            if source.exists():
                shutil.copy2(source, target / "eval" / name)
                manifest["eval_files"].append(name)
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"backup written to {target}")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
