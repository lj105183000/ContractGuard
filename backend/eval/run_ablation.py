"""Compare available evaluation variants without inventing missing runs.

Typical workflow:
  uv run python eval/run_eval.py --mode rules
  uv run python eval/run_eval.py --mode full --route deepseek-first
  uv run python eval/run_eval.py --mode full --route deepseek-first \
      --disable-rag --output-suffix full_no_rag
  uv run python eval/run_ablation.py

HITL is intentionally not treated as a P/R ablation: it changes the review
workflow/export gate, not the finding set. Workflow coverage is in tests.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]
JSON_PATH = BACKEND / "eval" / "results_ablation.json"
REPORT_PATH = BACKEND / "eval" / "report_ablation.md"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def variant_metrics(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    overall = payload.get("overall") or {}
    quality = overall.get("quality") or {}
    return {
        "precision": overall.get("precision"),
        "recall": overall.get("recall"),
        "f1": overall.get("f1"),
        "tp": overall.get("tp"),
        "fp": overall.get("fp"),
        "fn": overall.get("fn"),
        "evidence_valid_rate": overall.get("evidence_valid_rate"),
        "citation_valid_rate": overall.get("citation_valid_rate"),
        "confirmed_precision": quality.get("confirmed_precision"),
        "advisory_precision": quality.get("advisory_precision"),
        "unsupported_rate": quality.get("unsupported_rate"),
        "uncited_rate": quality.get("uncited_rate"),
        "high_risk_fp": quality.get("high_risk_fp"),
        "high_risk_fn": quality.get("high_risk_fn"),
    }


def _delta(left: dict | None, right: dict | None, keys: list[str]) -> dict:
    if left is None or right is None:
        return {}
    result = {}
    for key in keys:
        lvalue, rvalue = left.get(key), right.get(key)
        if isinstance(lvalue, (int, float)) and isinstance(rvalue, (int, float)):
            result[key] = round(rvalue - lvalue, 4)
    return result


def build_ablation(
    rules: dict | None,
    full: dict | None,
    full_no_rag: dict | None,
    retrieval: dict | None,
) -> dict:
    rules_m = variant_metrics(rules)
    full_m = variant_metrics(full)
    no_rag_m = variant_metrics(full_no_rag)
    keys = [
        "precision",
        "recall",
        "f1",
        "high_risk_fp",
        "unsupported_rate",
        "uncited_rate",
    ]
    retrieval_rows = []
    if retrieval:
        for config in retrieval.get("configs", []):
            overall = config.get("overall") or {}
            retrieval_rows.append(
                {
                    "name": config.get("name"),
                    "retrieval_mode": config.get("retrieval_mode"),
                    "rerank_mode": config.get("rerank_mode"),
                    "hit@10": overall.get("hit@10"),
                    "recall@10": overall.get("recall@10"),
                    "mrr@10": overall.get("mrr@10"),
                    "ndcg@10": overall.get("ndcg@10"),
                }
            )
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "variants": {
            "rules_only": rules_m,
            "full_rag": full_m,
            "full_no_rag": no_rag_m,
        },
        "deltas": {
            "full_rag_minus_rules_only": _delta(rules_m, full_m, keys),
            "full_no_rag_minus_full_rag": _delta(full_m, no_rag_m, keys),
        },
        "retrieval": retrieval_rows,
        "notes": [
            "rules_only 不需要 LLM；full_rag 使用当前 graph 的 LLM + RAG。",
            "full_no_rag 需要先运行 --disable-rag --output-suffix full_no_rag；"
            "它仍调用 LLM，但不提供检索到的法条/Playbook。",
            "rerank 对比来自 run_retrieval_eval 的各配置；需要配置 Key 才有 rerank 行。",
            "HITL 不改变 findings，因此不作为 P/R 消融项；它只影响复核/导出流程。",
        ],
    }


def write_report(payload: dict, path: Path) -> None:
    lines = ["# ContractGuard 消融对比", "", f"- 生成时间：{payload['generated_at']}", ""]
    labels = [
        ("rules_only", "规则 only"),
        ("full_rag", "规则 + Agent + RAG"),
        ("full_no_rag", "规则 + Agent（无 RAG 上下文）"),
    ]
    lines.extend(
        [
            "## 风险识别",
            "",
            "| 变体 | P | R | F1 | TP/FP/FN | 高风险 FP | 无证据率 | 无法条引用率 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, label in labels:
        item = payload["variants"].get(key)
        if not item:
            lines.append(f"| {label} | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | {item['precision'] or 0:.1%} | {item['recall'] or 0:.1%} | "
            f"{item['f1'] or 0:.1%} | {item['tp']}/{item['fp']}/{item['fn']} | "
            f"{item['high_risk_fp']} | {item['unsupported_rate'] or 0:.1%} | "
            f"{item['uncited_rate'] or 0:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Delta",
            "",
            f"- full - rules：`{payload['deltas']['full_rag_minus_rules_only']}`",
            f"- no-RAG - full：`{payload['deltas']['full_no_rag_minus_full_rag']}`",
            "",
            "## 检索",
            "",
            "| 配置 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["retrieval"]:
        lines.append(
            f"| {row['name']} | {row['hit@10'] or 0:.1%} | {row['recall@10'] or 0:.1%} | "
            f"{row['mrr@10'] or 0:.3f} | {row['ndcg@10'] or 0:.3f} |"
        )
    lines.extend(["", "## 说明", ""] + [f"- {note}" for note in payload["notes"]] + [""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", default=str(BACKEND / "eval" / "results_rules.json"))
    parser.add_argument("--full", default=str(BACKEND / "eval" / "results_full.json"))
    parser.add_argument(
        "--full-no-rag",
        default=str(BACKEND / "eval" / "results_full_no_rag.json"),
    )
    parser.add_argument(
        "--retrieval", default=str(BACKEND / "eval" / "results_retrieval.json")
    )
    parser.add_argument("--out", default=str(JSON_PATH))
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args()

    payload = build_ablation(
        _load(Path(args.rules)),
        _load(Path(args.full)),
        _load(Path(args.full_no_rag)),
        _load(Path(args.retrieval)),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(payload, report_path)
    print(f"写入 {args.out} 和 {args.report}")


if __name__ == "__main__":
    main()
