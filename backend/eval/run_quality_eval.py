"""Aggregate human review scores for legal relevance, evidence, and suggestions.

This is intentionally separate from risk P/R/F1: a finding can be a true
positive and still cite an irrelevant article or propose an unusable clause.
Scores are recorded by reviewers after seeing the system output.

Input is JSON or JSONL; each record:
  {
    "contract_id": "real_01",
    "finding_id": "llm-...",
    "reviewer": "legal_a",
    "legal_relevance": 4,        # 1 (irrelevant) .. 5 (directly relevant)
    "evidence_support": 5,       # 1 (unsupported) .. 5 (verbatim + sufficient)
    "suggestion_usability": 3,   # 1 (unusable) .. 5 (usable as-is)
    "hallucinated": false,
    "notes": ""
  }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.gates import check_max, check_min, enforce  # noqa: E402
from eval.stats import percentile  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
JSON_PATH = BACKEND / "eval" / "results_quality.json"
REPORT_PATH = BACKEND / "eval" / "report_quality.md"
METRICS = ("legal_relevance", "evidence_support", "suggestion_usability")


def load_scores(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("scores 必须是数组或 JSONL")
    return data


def validate(records: list[dict]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, 1):
        for key in ("contract_id", "finding_id", "reviewer"):
            if not str(record.get(key) or "").strip():
                errors.append(f"第 {index} 条缺少 {key}")
        reviewer = str(record.get("reviewer") or "")
        finding_id = str(record.get("finding_id") or "")
        key = (reviewer, finding_id)
        if reviewer and finding_id:
            if key in seen:
                errors.append(f"第 {index} 条重复评分：{reviewer} / {finding_id}")
            seen.add(key)
        for metric in METRICS:
            value = record.get(metric)
            if not isinstance(value, int) or not 1 <= value <= 5:
                errors.append(f"第 {index} 条 {metric} 必须是 1–5 的整数")
        if not isinstance(record.get("hallucinated"), bool):
            errors.append(f"第 {index} 条 hallucinated 必须是布尔值")
    return errors


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "p25": None, "p50": None, "p75": None}
    return {
        "n": len(values),
        "mean": round(sum(values) / len(values), 2),
        "p25": round(percentile(values, 0.25), 2),
        "p50": round(percentile(values, 0.50), 2),
        "p75": round(percentile(values, 0.75), 2),
    }


def summarize(records: list[dict]) -> dict:
    summary = {
        "reviews": len(records),
        "unique_findings": len({item["finding_id"] for item in records}),
        "reviewers": sorted({item["reviewer"] for item in records}),
        "contracts": sorted({item["contract_id"] for item in records}),
        "metrics": {},
        "hallucination_rate": round(
            sum(1 for item in records if item["hallucinated"]) / len(records), 4
        )
        if records
        else None,
        "suggestion_usable_rate": round(
            sum(1 for item in records if item["suggestion_usability"] >= 4)
            / len(records),
            4,
        )
        if records
        else None,
        "suggestion_unusable_rate": round(
            sum(1 for item in records if item["suggestion_usability"] <= 2)
            / len(records),
            4,
        )
        if records
        else None,
        "low_legal_relevance_rate": round(
            sum(1 for item in records if item["legal_relevance"] <= 2)
            / len(records),
            4,
        )
        if records
        else None,
        "per_contract": {},
    }
    for metric in METRICS:
        summary["metrics"][metric] = _stats(
            [float(item[metric]) for item in records]
        )
    for contract_id in summary["contracts"]:
        subset = [item for item in records if item["contract_id"] == contract_id]
        summary["per_contract"][contract_id] = {
            "reviews": len(subset),
            **{
                metric: _stats([float(item[metric]) for item in subset])
                for metric in METRICS
            },
        }
    return summary


def write_report(summary: dict, path: Path) -> None:
    lines = [
        "# ContractGuard 人工质量评分",
        "",
        f"- 评分记录：{summary['reviews']}；唯一 finding：{summary['unique_findings']}",
        f"- 评分人：{', '.join(summary['reviewers']) or '—'}",
        f"- 合同：{', '.join(summary['contracts']) or '—'}",
        "",
        "| 指标 | n | mean | p25 | p50 | p75 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for metric, stats in summary["metrics"].items():
        lines.append(
            f"| {metric} | {stats['n']} | {stats['mean']} | {stats['p25']} | "
            f"{stats['p50']} | {stats['p75']} |"
        )
    lines.extend(
        [
            "",
            "## 比例指标",
            "",
            f"- 法条相关性低（≤2）：{summary['low_legal_relevance_rate'] or 0:.1%}",
            f"- 建议可直接使用（≥4）：{summary['suggestion_usable_rate'] or 0:.1%}",
            f"- 建议不可用（≤2）：{summary['suggestion_unusable_rate'] or 0:.1%}",
            f"- 幻觉（人工标记）：{summary['hallucination_rate'] or 0:.1%}",
            "",
            "> 这些分数由人工评审产生，不是模型自评；没有评分数据时不能声称质量指标。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", required=True)
    parser.add_argument("--out", default=str(JSON_PATH))
    parser.add_argument("--report", default=str(REPORT_PATH))
    parser.add_argument("--min-legal-relevance", type=float, default=None)
    parser.add_argument("--min-evidence-support", type=float, default=None)
    parser.add_argument("--min-suggestion-usability", type=float, default=None)
    parser.add_argument("--max-hallucination-rate", type=float, default=None)
    args = parser.parse_args()

    records = load_scores(Path(args.scores))
    errors = validate(records)
    if errors:
        print("评分文件校验失败：")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    if not records:
        print("没有评分记录")
        raise SystemExit(1)
    summary = summarize(records)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(summary, report_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"写入 {out} 和 {args.report}")
    enforce(
        [
            check_min(
                "legal_relevance.mean",
                summary["metrics"]["legal_relevance"]["mean"],
                args.min_legal_relevance,
            ),
            check_min(
                "evidence_support.mean",
                summary["metrics"]["evidence_support"]["mean"],
                args.min_evidence_support,
            ),
            check_min(
                "suggestion_usability.mean",
                summary["metrics"]["suggestion_usability"]["mean"],
                args.min_suggestion_usability,
            ),
            check_max(
                "hallucination_rate",
                summary["hallucination_rate"],
                args.max_hallucination_rate,
            ),
        ]
    )


if __name__ == "__main__":
    main()
