"""Restore a backup created by backup_db.py.

Stop the backend before restoring. This is intentionally explicit and requires
--yes because it overwrites the live SQLite files.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import AUDIT_DIR, FEEDBACK_DIR, RUNS_DIR, UPLOAD_DIR, settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--include-uploads", action="store_true")
    parser.add_argument("--include-feedback", action="store_true")
    parser.add_argument("--include-traces", action="store_true")
    parser.add_argument("--include-eval", action="store_true")
    parser.add_argument("--include-audit", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to overwrite live data without --yes.")

    source = Path(args.backup_dir).expanduser()
    if not source.is_dir():
        raise SystemExit(f"backup dir not found: {source}")

    for name, target in (
        ("jobs.sqlite3", Path(settings.job_db_path).expanduser()),
        ("checkpoints.sqlite3", Path(settings.checkpoint_db_path).expanduser()),
    ):
        backup = source / name
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            print(f"restored {name} -> {target}")
    if args.include_uploads:
        uploads = source / "uploads"
        if uploads.exists():
            shutil.copytree(uploads, UPLOAD_DIR, dirs_exist_ok=True)
            print(f"restored uploads -> {UPLOAD_DIR}")
    if args.include_feedback:
        feedback = source / "feedback" / "feedback.jsonl"
        if feedback.exists():
            FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(feedback, FEEDBACK_DIR / "feedback.jsonl")
            print(f"restored feedback -> {FEEDBACK_DIR}")
    if args.include_traces:
        runs = source / "runs"
        if runs.exists():
            shutil.copytree(runs, RUNS_DIR, dirs_exist_ok=True)
            print(f"restored traces -> {RUNS_DIR}")
    if args.include_eval:
        eval_backup = source / "eval"
        if eval_backup.exists():
            eval_target = Path(__file__).resolve().parents[1] / "eval"
            shutil.copytree(eval_backup, eval_target, dirs_exist_ok=True)
            print(f"restored eval artifacts -> {eval_target}")
    if args.include_audit:
        audit_backup = source / "audit"
        if audit_backup.exists():
            shutil.copytree(audit_backup, AUDIT_DIR, dirs_exist_ok=True)
            print(f"restored audit tombstones -> {AUDIT_DIR}")


if __name__ == "__main__":
    main()
