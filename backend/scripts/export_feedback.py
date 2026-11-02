"""Export feedback JSONL with provenance and orphan reporting.

Usage (backend directory):
    uv run python scripts/export_feedback.py --out-dir ../exports/feedback
    uv run python scripts/export_feedback.py --out-dir /tmp/fb --formats jsonl,csv,sqlite
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import FEEDBACK_DIR, settings  # noqa: E402
from app.services.feedback_store import iter_records  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]

SQLITE_COLUMNS = [
    "schema_version",
    "feedback_id",
    "job_id",
    "finding_id",
    "rule_id",
    "category",
    "severity",
    "decision",
    "decision_scope",
    "contract_sha256",
    "mode",
    "llm_route",
    "llm_models",
    "prompt_version",
    "kb_version",
    "source",
    "confidence_tier",
    "at",
    "legacy",
    "is_orphan",
    "orphan_reason",
]


def _load_job_ids(db_path: Path) -> tuple[set[str] | None, str | None]:
    if not db_path.exists():
        return None, "db_missing"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT job_id FROM jobs").fetchall()
        finally:
            conn.close()
        return {row[0] for row in rows}, None
    except sqlite3.Error as exc:
        return None, f"db_error:{exc}"


def _normalize(
    record: dict,
    job_ids: set[str] | None,
    *,
    include_notes: bool,
    exported_at: str,
) -> dict:
    job_id = str(record.get("job_id") or "")
    if job_ids is None:
        is_orphan = False
        orphan_reason = None
    elif job_id not in job_ids:
        is_orphan = True
        orphan_reason = "job_missing"
    else:
        is_orphan = False
        orphan_reason = None
    normalized = {
        "schema_version": record.get("schema_version", 1),
        "feedback_id": record.get("feedback_id"),
        "job_id": job_id,
        "finding_id": record.get("finding_id"),
        "rule_id": record.get("rule_id"),
        "category": record.get("category"),
        "severity": record.get("severity"),
        "decision": record.get("decision"),
        "decision_scope": record.get("decision_scope"),
        "contract_sha256": record.get("contract_sha256"),
        "mode": record.get("mode"),
        "llm_route": record.get("llm_route"),
        "llm_models": record.get("llm_models") or [],
        "prompt_version": record.get("prompt_version"),
        "kb_version": record.get("kb_version"),
        "source": record.get("source"),
        "confidence_tier": record.get("confidence_tier"),
        "at": record.get("at"),
        "legacy": int(record.get("schema_version", 1)) < 2,
        "is_orphan": is_orphan,
        "orphan_reason": orphan_reason,
        "exported_at": exported_at,
    }
    if include_notes:
        normalized["note"] = record.get("note", "")
    return normalized


def export(
    feedback_path: Path,
    db_path: Path,
    out_dir: Path,
    formats: list[str],
    *,
    include_notes: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    job_ids, db_error = _load_job_ids(db_path)
    total_lines = malformed = decrypt_errors = legacy = orphan = encrypted = 0
    records: list[dict] = []
    for _line, raw, is_encrypted, error in iter_records(feedback_path):
        total_lines += 1
        encrypted += int(is_encrypted)
        if raw is None:
            if error and error.startswith("decrypt_failed"):
                decrypt_errors += 1
            else:
                malformed += 1
            continue
        item = _normalize(
            raw,
            job_ids,
            include_notes=include_notes,
            exported_at=exported_at,
        )
        records.append(item)
        legacy += int(item["legacy"])
        orphan += int(item["is_orphan"])

    report = {
        "generated_at": exported_at,
        "db_available": job_ids is not None,
        "db_error": db_error,
        "total_lines": total_lines,
        "exported_records": len(records),
        "malformed_lines": malformed,
        "encrypted_records": encrypted,
        "decrypt_errors": decrypt_errors,
        "legacy_records": legacy,
        "notes_included": include_notes,
        "job_present": len(records) - orphan,
        "orphan_records": orphan,
        "orphan_rate": round(orphan / len(records), 4) if records else 0.0,
        "orphans_by_reason": {"job_missing": orphan} if orphan else {},
        "sample_orphan_job_ids": [
            item["job_id"] for item in records if item["is_orphan"]
        ][:20],
    }
    (out_dir / "orphans.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if "jsonl" in formats:
        with (out_dir / "feedback.jsonl").open("w", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    if "csv" in formats:
        with (out_dir / "feedback.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SQLITE_COLUMNS)
            writer.writeheader()
            for item in records:
                row = dict(item)
                row["llm_models"] = ";".join(item.get("llm_models") or [])
                writer.writerow({key: row.get(key) for key in SQLITE_COLUMNS})
    if "sqlite" in formats:
        conn = sqlite3.connect(out_dir / "feedback.sqlite3")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS feedback_export ("
                + ", ".join(f"{column} TEXT" for column in SQLITE_COLUMNS)
                + ")"
            )
            conn.execute("DELETE FROM feedback_export")
            for item in records:
                conn.execute(
                    f"INSERT INTO feedback_export ({', '.join(SQLITE_COLUMNS)}) "
                    f"VALUES ({', '.join(['?'] * len(SQLITE_COLUMNS))})",
                    [
                        (
                            json.dumps(item.get(column), ensure_ascii=False)
                            if column == "llm_models"
                            else item.get(column)
                        )
                        for column in SQLITE_COLUMNS
                    ],
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_job "
                "ON feedback_export(job_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_orphan "
                "ON feedback_export(is_orphan)"
            )
            conn.commit()
        finally:
            conn.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feedback", default=str(FEEDBACK_DIR / "feedback.jsonl"))
    parser.add_argument("--db", default=str(settings.job_db_path))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--formats",
        default="jsonl",
        help="逗号分隔：jsonl,csv,sqlite",
    )
    parser.add_argument("--include-notes", action="store_true")
    args = parser.parse_args()
    formats = [item.strip() for item in args.formats.split(",") if item.strip()]
    invalid = set(formats) - {"jsonl", "csv", "sqlite"}
    if invalid:
        raise SystemExit(f"不支持的格式：{sorted(invalid)}")
    report = export(
        Path(args.feedback),
        Path(args.db),
        Path(args.out_dir),
        formats,
        include_notes=args.include_notes,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
