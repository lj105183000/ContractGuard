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
DEFAULT_REPORT = BACKEND / "eval" / "report_iaa.json"
DEFAULT_MARKDOWN = BACKEND / "eval" / "report_iaa.md"


def load_labels(path: Path) -> list[EvalLabel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalLabel.model_validate(item) for item in data]


def load_universe(path: Path) -> set[tuple]:
    """Load the fixed candidate universe used as kappa's negative space.

    Accepted JSON shapes:
      [{"clause_id": "cl_003", "clause_article": "第三条",
        "category": "payment", "rule_id": "PAY-001"}, ...]
      [["cl_003", "payment", "PAY-001"], ...]
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("universe 必须是 JSON 数组")
    keys: set[tuple] = set()
    for item in data:
        if isinstance(item, dict):
            anchor = item.get("clause_id") or item.get("clause_article") or ""
            category = item.get("category") or ""
            rule_id = item.get("rule_id") or ""
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            anchor, category = item[0], item[1]
            rule_id = item[2] if len(item) >= 3 else ""
        else:
            raise ValueError("universe 项必须是对象或 [anchor, category, rule_id]")
        if not anchor or not category:
            raise ValueError("universe 项缺少 clause_id/clause_article 或 category")
        keys.add((str(anchor), str(category), str(rule_id or "")))
    return keys


def write_markdown(payload: dict, path: Path) -> None:
    kappa = payload.get("cohen_kappa")
    lines = [
        "# ContractGuard 标注者一致性（IAA）",
        "",
        f"- 标注 A：{payload['labels_a']} 条；标注 B：{payload['labels_b']} 条",
        f"- 一致：{payload['both']}；仅 A：{payload['only_a']}；仅 B：{payload['only_b']}",
        f"- Kappa：{kappa if kappa is not None else 'N/A'}（口径：{payload['kappa_scope']}）",
        f"- Universe：{payload.get('universe_size', 0)} 条候选键",
        f"- Jaccard：{payload.get('jaccard')}；Positive agreement：{payload.get('positive_agreement')}",
        f"- 方法：{payload['method']}",
        "",
    ]
    if payload["kappa_scope"] == "union_scope_optimistic":
        lines.extend(
            [
                "> ⚠️ 未提供固定 universe。此 kappa 只在“双方任一方提出的正例并集”上计算，"
                "双方共同漏掉的风险不在分母中，结果偏乐观，不应作为正式 IAA 报告。"
                "请用 `--universe` 传入候选键全集后重跑。",
                "",
            ]
        )
    if payload.get("min_kappa_failed"):
        lines.extend([f"> ❌ 未达到最低 kappa：{payload['min_kappa']}", ""])
    elif payload.get("min_kappa") is not None:
        lines.extend([f"> ✅ 达到最低 kappa：{payload['min_kappa']}", ""])
    lines.extend(["## 分歧", "", "| 侧 | clause | category | rule_id |", "|---|---|---|---|"])
    for item in payload.get("disagreements", []):
        key = item.get("key") or ["", "", ""]
        lines.append(
            f"| {item.get('side')} | {key[0]} | {key[1]} | "
            f"{key[2] if len(key) > 2 else ''} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels-a", required=True)
    parser.add_argument("--labels-b", required=True)
    parser.add_argument(
        "--universe",
        default=None,
        help="固定候选键集合 JSON；提供后才算可报告的 fixed-universe kappa",
    )
    parser.add_argument("--out", default=str(DEFAULT_REPORT), help="JSON 输出路径")
    parser.add_argument(
        "--markdown-out", default=str(DEFAULT_MARKDOWN), help="Markdown 输出路径"
    )
    parser.add_argument("--min-kappa", type=float, default=None)
    args = parser.parse_args()

    labels_a = load_labels(Path(args.labels_a))
    labels_b = load_labels(Path(args.labels_b))
    universe = load_universe(Path(args.universe)) if args.universe else None
    agreement = compare_labels(labels_a, labels_b, universe_keys=universe)
    payload = {
        **agreement.model_dump(),
        "labels_a": len(labels_a),
        "labels_b": len(labels_b),
        "annotators_a": label_counts(labels_a),
        "annotators_b": label_counts(labels_b),
        "universe_path": args.universe,
        "method": (
            "binary presence on (clause anchor, category, rule_id); "
            "kappa over fixed candidate universe"
            if universe is not None
            else "binary presence on (clause anchor, category, rule_id); "
            "kappa over positive union only (optimistic)"
        ),
        "min_kappa": args.min_kappa,
        "min_kappa_failed": (
            args.min_kappa is not None
            and (agreement.cohen_kappa is None or agreement.cohen_kappa < args.min_kappa)
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_out = Path(args.markdown_out)
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    write_markdown(payload, markdown_out)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"写入 {out} 和 {markdown_out}")
    if payload["min_kappa_failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
