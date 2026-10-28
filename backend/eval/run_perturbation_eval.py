"""Rules-only robustness check over deterministic text perturbations.

This is not a proof of real-world robustness: it only measures whether the
current rules survive renamed parties, reformatted amounts, light OCR noise and
clause reordering on the synthetic set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsing.clause_splitter import split_clauses  # noqa: E402
from app.parsing.contract_parser import parse_contract  # noqa: E402
from app.parsing.facts import extract_heuristic_facts  # noqa: E402
from app.rag.corpus import load_kb  # noqa: E402
from app.rules.engine import evaluate_rules  # noqa: E402
from app.services.confidence import apply_confidence_tier  # noqa: E402
from eval.perturb import apply_text_perturbation  # noqa: E402
from eval.run_eval import SAMPLES_DIR, load_labels, rules_run, score  # noqa: E402
from eval.stats import prf_counts  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
JSON_PATH = BACKEND / "eval" / "results_perturbation.json"
REPORT_PATH = BACKEND / "eval" / "report_perturbation.md"
PERTURBATIONS = ("party_names", "amount_formats", "ocr_noise", "reordered_clauses")


def _totals(results: list[dict]) -> dict:
    tp = sum(item["score"]["tp"] for item in results)
    fp = sum(item["score"]["fp"] for item in results)
    fn = sum(item["score"]["fn"] for item in results)
    return {"tp": tp, "fp": fp, "fn": fn, **prf_counts(tp, fp, fn)}


def main() -> None:
    samples = yaml.safe_load(
        (SAMPLES_DIR / "samples.yaml").read_text(encoding="utf-8")
    )
    kb = load_kb()
    baseline_results = []
    rows: dict[str, list[dict]] = {name: [] for name in PERTURBATIONS}
    per_sample: list[dict] = []

    for sample in samples:
        labels = load_labels(sample["id"])
        path = SAMPLES_DIR / sample["filename"]
        parsed = parse_contract(path, sample["filename"])
        baseline_findings, _duration = rules_run(sample, labels)
        baseline_score = score(labels, baseline_findings)
        baseline_results.append({"score": baseline_score})

        sample_row = {
            "sample_id": sample["id"],
            "baseline": baseline_score,
            "perturbations": {},
        }
        for kind in PERTURBATIONS:
            perturbed = apply_text_perturbation(parsed, kind)
            clauses = split_clauses(perturbed)
            facts = extract_heuristic_facts(perturbed.text, clauses)
            findings = evaluate_rules(
                kb.rules, clauses, facts, perturbed.text
            ).findings
            for finding in findings:
                apply_confidence_tier(finding, mark_needs_human=False)
            result = score(labels, findings)
            rows[kind].append({"score": result})
            sample_row["perturbations"][kind] = result
        per_sample.append(sample_row)

    baseline = _totals(baseline_results)
    summary = {"baseline": baseline, "perturbations": {}, "samples": per_sample}
    for kind in PERTURBATIONS:
        summary["perturbations"][kind] = _totals(rows[kind])

    JSON_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(summary, len(samples))
    print(f"写入 {JSON_PATH} 和 {REPORT_PATH}")


def write_report(summary: dict, sample_count: int) -> None:
    baseline = summary["baseline"]
    lines = [
        "# 合成合同扰动稳定性",
        "",
        f"- 合同数：{sample_count}；规则模式，不调用 LLM。",
        "- 扰动是确定性的文本级变换（改名、金额格式、轻 OCR 噪声、条款倒序），"
        "标签与阈值不变；不是真实 OCR/排版鲁棒性证明。",
        "",
        "## 总体",
        "",
        "| 版本 | TP | FP | FN | Precision | Recall | F1 | ΔF1（pp） |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| baseline | {baseline['tp']} | {baseline['fp']} | {baseline['fn']} | "
        f"{baseline['precision']:.1%} | {baseline['recall']:.1%} | "
        f"{baseline['f1']:.1%} | — |",
    ]
    for kind, stats in summary["perturbations"].items():
        delta = (stats["f1"] - baseline["f1"]) * 100
        lines.append(
            f"| {kind} | {stats['tp']} | {stats['fp']} | {stats['fn']} | "
            f"{stats['precision']:.1%} | {stats['recall']:.1%} | "
            f"{stats['f1']:.1%} | {delta:+.1f} |"
        )
    lines.extend(
        [
            "",
            "## 解释",
            "",
            "- `amount_formats` 把“万元/%”改成“元/带空格百分号”，可暴露事实正则的格式依赖。",
            "- `party_names` 替换甲乙方法定名称，检查规则是否错误依赖具体公司名。",
            "- `ocr_noise` 只在非条款标题行替换少量汉字，模拟扫描噪声；不代表真实 OCR 错误分布。",
            "- `reordered_clauses` 反转条款顺序但保留条款标题，检查标签是否过度依赖文档顺序。",
            "- 指标下降不等于线上一定失败；这里只做小样本、规则层的回归信号。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
