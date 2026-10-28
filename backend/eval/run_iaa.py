"""Compute inter-annotator agreement between two label files.

Usage (backend directory):
    uv run python eval/run_iaa.py \
      --labels-a ../private/labels/real_01.annotator_a.json \
      --labels-b ../private/labels/real_01.annotator_b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas import EvalLabel  # noqa: E402
from eval.iaa import compare_labels, label_counts  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = BACKEND / "eval" / "report_iaa.md"


def load_labels(path: Path) -> list[EvalLabel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalLabel.model_validate(item) for item in data]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels-a", required=True)
    parser.add_argument("--labels-b", required=True)
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    labels_a = load_labels(Path(args.labels_a))
    labels_b = load_labels(Path(args.labels_b))
    agreement = compare_labels(labels_a, labels_b)
    payload = {
        **agreement.model_dump(),
        "labels_a": len(labels_a),
        "labels_b": len(labels_b),
        "annotators_a": label_counts(labels_a),
        "annotators_b": label_counts(labels_b),
        "method": "binary presence on (clause anchor, category, rule_id)",
    }
    out = Path(args.out)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"写入 {out}")


if __name__ == "__main__":
    main()
