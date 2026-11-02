"""Validate public evaluation assets (samples, labels, retrieval, real manifest).

Usage (backend directory):
    uv run python scripts/validate_eval_assets.py

Public real manifests must stay anonymous: no filenames, paths, hashes, or
private labels. Private real data is validated separately by the annotation
workflow and is intentionally unavailable in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.corpus import load_kb  # noqa: E402
from app.schemas import EvalLabel  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES_DIR = BACKEND / "data" / "samples"
LABELS_DIR = BACKEND / "eval" / "labels"
REAL_MANIFEST = BACKEND / "eval" / "real" / "contracts.yaml"
RETRIEVAL_QUERIES = BACKEND / "eval" / "retrieval" / "queries.yaml"
BADCASES = BACKEND / "eval" / "badcases.yaml"

ALLOWED_REAL_FIELDS = {"id", "title", "scope", "split", "pages", "text_layer", "notes"}
ALLOWED_SPLITS = {"dev", "holdout"}
ALLOWED_SCOPES = {"in_scope", "adjacent", "out_of_scope"}


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate() -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts: dict[str, int] = {}
    kb = load_kb()
    legal_ids = {article.id for article in kb.legal_articles}
    rule_ids = {rule.id for rule in kb.rules}

    samples = _load_yaml(SAMPLES_DIR / "samples.yaml") or []
    sample_ids = [item.get("id") for item in samples]
    if len(sample_ids) != len(set(sample_ids)):
        errors.append("samples.yaml 存在重复 sample id")
    counts["samples"] = len(samples)
    label_total = 0
    seen_labels: set[str] = set()
    for sample in samples:
        sample_id = sample.get("id")
        filename = sample.get("filename")
        if not filename or not (SAMPLES_DIR / filename).exists():
            errors.append(f"{sample_id} 合同文件缺失")
        label_path = LABELS_DIR / f"{sample_id}.json"
        if not label_path.exists():
            errors.append(f"{sample_id} 缺少标签文件")
            continue
        try:
            raw = json.loads(label_path.read_text(encoding="utf-8"))
            labels = [EvalLabel.model_validate(item) for item in raw]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sample_id} 标签无法解析：{exc}")
            continue
        for label in labels:
            if label.id in seen_labels:
                errors.append(f"重复 label id：{label.id}")
            seen_labels.add(label.id)
            if label.rule_id and label.rule_id not in rule_ids:
                errors.append(f"{label.id} 引用了不存在的规则：{label.rule_id}")
        label_total += len(labels)
    counts["labels"] = label_total
    counts["label_files"] = len(list(LABELS_DIR.glob("*.json")))

    real = _load_yaml(REAL_MANIFEST) or []
    real_ids = [item.get("id") for item in real]
    if len(real_ids) != len(set(real_ids)):
        errors.append("真实合同公共 manifest 存在重复 id")
    for item in real:
        unknown = set(item) - ALLOWED_REAL_FIELDS
        if unknown:
            errors.append(
                f"真实合同公共 manifest 含不允许字段 {sorted(unknown)}：{item.get('id')}"
            )
        if item.get("split") not in ALLOWED_SPLITS:
            errors.append(f"{item.get('id')} split 非法：{item.get('split')}")
        if item.get("scope") not in ALLOWED_SCOPES:
            errors.append(f"{item.get('id')} scope 非法：{item.get('scope')}")
    counts["real_public"] = len(real)

    queries = _load_yaml(RETRIEVAL_QUERIES) or []
    query_ids = [item.get("id") for item in queries]
    if len(query_ids) != len(set(query_ids)):
        errors.append("retrieval/queries.yaml 存在重复 query id")
    for item in queries:
        for law_id in item.get("relevant") or []:
            if law_id not in legal_ids:
                errors.append(f"{item.get('id')} 引用了不存在的法条：{law_id}")
    counts["retrieval_queries"] = len(queries)

    badcases = _load_yaml(BADCASES) or {}
    known = {
        item.get("label_id")
        for item in (badcases.get("rules") or {}).get("known_false_negatives") or []
    }
    missing = sorted(known - seen_labels)
    if missing:
        errors.append(f"badcases 引用了不存在的 label：{missing}")

    return errors, counts


def main() -> None:
    errors, counts = validate()
    print(
        "评测资产："
        + "，".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("评测资产校验通过。")


if __name__ == "__main__":
    main()
