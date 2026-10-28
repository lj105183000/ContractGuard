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

from app.config import UPLOAD_DIR, settings  # noqa: E402

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
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path(args.output_dir).expanduser() / stamp
    target.mkdir(parents=True, exist_ok=True)

    manifest = {"created_at": stamp, "files": [], "uploads": False}
    for name, source in (
        ("jobs.sqlite3", Path(settings.job_db_path).expanduser()),
        ("checkpoints.sqlite3", Path(settings.checkpoint_db_path).expanduser()),
    ):
        if backup_sqlite(source, target / name):
            manifest["files"].append(name)
    if args.include_uploads and UPLOAD_DIR.exists():
        shutil.copytree(UPLOAD_DIR, target / "uploads", dirs_exist_ok=True)
        manifest["uploads"] = True
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"backup written to {target}")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
