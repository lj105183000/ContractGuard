"""离线评测：规则基线 vs 完整多 Agent 流程。

用法（backend 目录）：
    uv run python eval/run_eval.py --mode rules
    uv run python eval/run_eval.py --mode full --limit 2   # 先小跑
    uv run python eval/run_eval.py --mode full             # 需要 live API Key

评测口径：
- 标签：8 份合成合同，48 个规则可检出点 + 3 个语义点；
- 规则标签按 rule_id + 条款匹配；
- 语义标签按 category + 条款 + 关键词匹配；
- 同一预测/标签只匹配一次；
- 指标是微平均；数据量很小，不代表真实泛化能力；
- 覆盖率按 source 与 confidence tier 分解，便于定位误报来源。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.graph.build import build_graph  # noqa: E402
from app.llm.profiles import (  # noqa: E402
    RouteError,
    chat_runtime,
    resolve_route,
)
from app.llm.trace import TraceCollector  # noqa: E402
from app.parsing.clause_splitter import split_clauses  # noqa: E402
from app.parsing.contract_parser import parse_contract  # noqa: E402
from app.parsing.facts import extract_heuristic_facts  # noqa: E402
from app.provenance import (  # noqa: E402
    APP_VERSION,
    SCHEMA_VERSION,
    command_provenance,
    git_provenance,
    profiles_provenance,
    prompt_provenance,
    python_version,
)
from app.rag.corpus import load_kb  # noqa: E402
from app.rag.retriever import get_retriever  # noqa: E402
from app.rules.engine import evaluate_rules  # noqa: E402
from app.schemas import EvalLabel, Finding, Source  # noqa: E402
from app.services.confidence import apply_confidence_tier  # noqa: E402
from app.services.summary import (  # noqa: E402
    compute_citation_metrics,
    compute_evidence_metrics,
)
from eval.gates import check_min, enforce  # noqa: E402
from eval.provenance import synthetic_dataset_provenance  # noqa: E402
from eval.stats import bootstrap_prf  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES_DIR = BACKEND / "data" / "samples"
LABELS_DIR = BACKEND / "eval" / "labels"
SEVERITIES = ("high", "medium", "low")


def load_labels(sample_id: str) -> list[EvalLabel]:
    path = LABELS_DIR / f"{sample_id}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalLabel.model_validate(item) for item in data]


def predicted_rule_id(finding: Finding) -> str | None:
    """Only the deterministic rule engine may satisfy a rule label.

    ``rule_hint`` is model self-reporting and is deliberately not used here;
    otherwise an LLM finding could claim a rule hit without the rule engine
    actually firing.
    """
    return finding.rule_id


def _normalize_anchor(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _anchor_matches(label: EvalLabel, finding: Finding) -> bool:
    if label.clause_id and finding.clause_id == label.clause_id:
        return True
    if label.quote:
        haystack = _normalize_anchor(finding.clause_text) + "\n" + _normalize_anchor(
            finding.evidence_quote
        )
        if _normalize_anchor(label.quote) in haystack:
            return True
    if not label.clause_id and not label.quote:
        return (finding.clause_article or "合同级") == (
            label.clause_article or ""
        )
    return False


def matches(label: EvalLabel, finding: Finding) -> bool:
    if not _anchor_matches(label, finding):
        return False
    if label.rule_id:
        return predicted_rule_id(finding) == label.rule_id
    if finding.category.value != label.category.value:
        return False
    if not label.keywords:
        return True
    haystack = f"{finding.evidence_quote}\n{finding.analysis}\n{finding.title}"
    return any(keyword in haystack for keyword in label.keywords)


def score(labels: list[EvalLabel], findings: list[Finding]) -> dict:
    matched_labels: set[int] = set()
    matched_findings: set[int] = set()
    pairs = []
    for label_index, label in enumerate(labels):
        for finding_index, finding in enumerate(findings):
            if finding_index in matched_findings:
                continue
            if matches(label, finding):
                matched_labels.add(label_index)
                matched_findings.add(finding_index)
                pairs.append(
                    {
                        "label_id": label.id,
                        "finding_id": finding.id,
                        "rule_id": predicted_rule_id(finding),
                        "rule_hint": finding.rule_hint,
                        "clause": label.clause_article,
                        "severity": label.severity.value,
                    }
                )
                break

    tp = len(pairs)
    fp = len(findings) - len(matched_findings)
    fn = len(labels) - len(matched_labels)
    severity_stats = {
        severity: {"tp": 0, "fp": 0, "fn": 0} for severity in SEVERITIES
    }
    for pair in pairs:
        severity_stats[pair["severity"]]["tp"] += 1
    for index, finding in enumerate(findings):
        if index not in matched_findings:
            severity_stats[finding.severity.value]["fp"] += 1
    for index, label in enumerate(labels):
        if index not in matched_labels:
            severity_stats[label.severity.value]["fn"] += 1

    def prf(tp_value: int, fp_value: int, fn_value: int) -> dict:
        precision = tp_value / (tp_value + fp_value) if tp_value + fp_value else 0.0
        recall = tp_value / (tp_value + fn_value) if tp_value + fn_value else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    def with_precision(stats: dict[str, dict[str, int]]) -> dict[str, dict]:
        result = {}
        for key, bucket in stats.items():
            total = bucket["tp"] + bucket["fp"]
            result[key] = {
                **bucket,
                "precision": round(bucket["tp"] / total, 4) if total else 0.0,
            }
        return result

    source_stats: dict[str, dict[str, int]] = {}
    tier_stats: dict[str, dict[str, int]] = {}
    for index, finding in enumerate(findings):
        source = finding.source.value
        tier = (
            finding.confidence_tier.value
            if finding.confidence_tier is not None
            else "unknown"
        )
        source_bucket = source_stats.setdefault(source, {"tp": 0, "fp": 0})
        tier_bucket = tier_stats.setdefault(tier, {"tp": 0, "fp": 0})
        hit = index in matched_findings
        source_bucket["tp" if hit else "fp"] += 1
        tier_bucket["tp" if hit else "fp"] += 1

    fp_findings = [
        finding
        for index, finding in enumerate(findings)
        if index not in matched_findings
    ]

    def count_by(items: list[Finding], key) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            value = key(item) or "unknown"
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items(), key=lambda pair: -pair[1]))

    fp_attribution = {
        "total": len(fp_findings),
        "by_source": count_by(fp_findings, lambda item: item.source.value),
        "by_severity": count_by(fp_findings, lambda item: item.severity.value),
        "by_category": count_by(fp_findings, lambda item: item.category.value),
        "by_agent": count_by(fp_findings, lambda item: item.agent),
        "with_rule_hint": sum(1 for item in fp_findings if item.rule_hint),
        "without_rule_hint": sum(1 for item in fp_findings if not item.rule_hint),
        "evidence_located": sum(
            1 for item in fp_findings if item.evidence_start is not None
        ),
        "evidence_missing": sum(
            1 for item in fp_findings if item.evidence_start is None
        ),
        "with_legal_basis": sum(1 for item in fp_findings if item.legal_basis_ids),
        "without_legal_basis": sum(
            1 for item in fp_findings if not item.legal_basis_ids
        ),
    }

    evidence_checked, evidence_valid, evidence_rate = compute_evidence_metrics(findings)
    citation_checked, citation_valid, citation_rate = compute_citation_metrics(findings)
    unsupported = [
        finding
        for finding in findings
        if finding.source in {Source.LLM, Source.RULE_LLM}
        and finding.evidence_start is None
    ]
    uncited = [
        finding
        for finding in findings
        if finding.source in {Source.LLM, Source.RULE_LLM}
        and not finding.legal_basis_ids
    ]
    finding_count = len(findings)
    quality = {
        "unsupported_findings": len(unsupported),
        "unsupported_rate": round(len(unsupported) / finding_count, 4)
        if finding_count
        else None,
        "uncited_findings": len(uncited),
        "uncited_rate": round(len(uncited) / finding_count, 4)
        if finding_count
        else None,
        "high_risk_fp": severity_stats["high"]["fp"],
        "high_risk_fn": severity_stats["high"]["fn"],
        "confirmed_precision": (
            round(
                tier_stats["confirmed"]["tp"]
                / (tier_stats["confirmed"]["tp"] + tier_stats["confirmed"]["fp"]),
                4,
            )
            if tier_stats.get("confirmed", {}).get("tp", 0)
            + tier_stats.get("confirmed", {}).get("fp", 0)
            else None
        ),
        "advisory_precision": (
            round(
                tier_stats["advisory"]["tp"]
                / (tier_stats["advisory"]["tp"] + tier_stats["advisory"]["fp"]),
                4,
            )
            if tier_stats.get("advisory", {}).get("tp", 0)
            + tier_stats.get("advisory", {}).get("fp", 0)
            else None
        ),
        "total_findings": finding_count,
    }

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        **prf(tp, fp, fn),
        "by_severity": {
            severity: {
                **stats,
                **prf(stats["tp"], stats["fp"], stats["fn"]),
            }
            for severity, stats in severity_stats.items()
        },
        "by_source": with_precision(source_stats),
        "by_confidence": with_precision(tier_stats),
        "fp_attribution": fp_attribution,
        "evidence_checked": evidence_checked,
        "evidence_valid": evidence_valid,
        "evidence_valid_rate": (
            round(evidence_rate, 4) if evidence_rate is not None else None
        ),
        "citation_checked": citation_checked,
        "citation_valid": citation_valid,
        "citation_valid_rate": (
            round(citation_rate, 4) if citation_rate is not None else None
        ),
        "quality": quality,
        "matches": pairs,
        "unmatched_findings": [
            finding.model_dump(mode="json")
            for index, finding in enumerate(findings)
            if index not in matched_findings
        ],
        "unmatched_labels": [
            label.model_dump(mode="json")
            for index, label in enumerate(labels)
            if index not in matched_labels
        ],
    }


def rules_run(sample: dict, labels: list[EvalLabel]) -> tuple[list[Finding], float]:
    started = time.perf_counter()
    path = SAMPLES_DIR / sample["filename"]
    parsed = parse_contract(path, sample["filename"])
    clauses = split_clauses(parsed)
    facts = extract_heuristic_facts(parsed.text, clauses)
    kb = load_kb()
    result = evaluate_rules(kb.rules, clauses, facts, parsed.text)
    findings = result.findings
    for finding in findings:
        apply_confidence_tier(finding, mark_needs_human=False)
    return findings, time.perf_counter() - started


async def full_run(
    sample: dict,
    labels: list[EvalLabel],
    route_id: str | None = None,
    llm_mode: str = "mock",
    disable_rag: bool = False,
) -> tuple[list[Finding], float, dict]:
    started = time.perf_counter()
    trace = TraceCollector()
    graph = build_graph()
    path = SAMPLES_DIR / sample["filename"]
    state = {
        "job_id": f"eval-{sample['id']}",
        "filename": sample["filename"],
        "source_path": str(path),
        "llm_route": route_id,
        "llm_mode": llm_mode,
        "disable_rag": disable_rag,
        "trace": trace,
    }
    result = await graph.ainvoke(state, config={"recursion_limit": 30})
    report = result.get("report")
    if report is None:
        raise RuntimeError(f"{sample['id']} 没有生成报告")
    findings = report.findings
    duration = time.perf_counter() - started
    retriever = get_retriever()
    usage = trace.usage(
        duration_ms=int(duration * 1000),
        mode=llm_mode,
        embedding_mode=retriever.retrieval_mode,
        rerank_mode=retriever.rerank_mode,
        llm_route=route_id,
        llm_route_label=(
            resolve_route(route_id, require_available=False).label
            if route_id
            else None
        ),
    )
    return findings, duration, usage.model_dump(mode="json")


def aggregate(sample_results: list[dict]) -> dict:
    tp = sum(item["score"]["tp"] for item in sample_results)
    fp = sum(item["score"]["fp"] for item in sample_results)
    fn = sum(item["score"]["fn"] for item in sample_results)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total_findings = sum(item["finding_count"] for item in sample_results)
    total_labels = sum(item["label_count"] for item in sample_results)
    citations = sum(item["citation_count"] for item in sample_results)
    valid_citations = sum(item["valid_citation_count"] for item in sample_results)
    evidence_checked = sum(item["evidence_checked"] for item in sample_results)
    evidence_valid = sum(item["evidence_valid"] for item in sample_results)

    source_totals: dict[str, dict[str, int]] = {}
    tier_totals: dict[str, dict[str, int]] = {}
    quality_totals = {
        "unsupported_findings": 0,
        "uncited_findings": 0,
        "high_risk_fp": 0,
        "high_risk_fn": 0,
        "total_findings": 0,
    }
    fp_attribution: dict = {
        "total": 0,
        "by_source": {},
        "by_severity": {},
        "by_category": {},
        "by_agent": {},
        "with_rule_hint": 0,
        "without_rule_hint": 0,
        "evidence_located": 0,
        "evidence_missing": 0,
        "with_legal_basis": 0,
        "without_legal_basis": 0,
    }
    for item in sample_results:
        score_data = item["score"]
        for key, bucket in score_data["by_source"].items():
            target = source_totals.setdefault(key, {"tp": 0, "fp": 0})
            target["tp"] += bucket["tp"]
            target["fp"] += bucket["fp"]
        for key, bucket in score_data["by_confidence"].items():
            target = tier_totals.setdefault(key, {"tp": 0, "fp": 0})
            target["tp"] += bucket["tp"]
            target["fp"] += bucket["fp"]
        quality = score_data.get("quality", {})
        for key in quality_totals:
            quality_totals[key] += int(quality.get(key, 0) or 0)
        attribution = score_data["fp_attribution"]
        fp_attribution["total"] += attribution["total"]
        for dimension in ("by_source", "by_severity", "by_category", "by_agent"):
            for key, value in attribution[dimension].items():
                bucket = fp_attribution[dimension]
                bucket[key] = bucket.get(key, 0) + value
        for key in (
            "with_rule_hint",
            "without_rule_hint",
            "evidence_located",
            "evidence_missing",
            "with_legal_basis",
            "without_legal_basis",
        ):
            fp_attribution[key] += attribution[key]

    def finalize(stats: dict[str, dict[str, int]]) -> dict[str, dict]:
        result = {}
        for key, bucket in stats.items():
            total = bucket["tp"] + bucket["fp"]
            result[key] = {
                **bucket,
                "precision": round(bucket["tp"] / total, 4) if total else 0.0,
            }
        return dict(sorted(result.items(), key=lambda pair: -pair[1]["tp"]))

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "findings": total_findings,
        "labels": total_labels,
        "evidence_checked": evidence_checked,
        "citation_checked": citations,
        "by_source": finalize(source_totals),
        "by_confidence": finalize(tier_totals),
        "fp_attribution": fp_attribution,
        "evidence_valid_rate": round(evidence_valid / evidence_checked, 4)
        if evidence_checked
        else None,
        "citation_valid_rate": round(valid_citations / citations, 4)
        if citations
        else None,
        "quality": {
            **quality_totals,
            "unsupported_rate": round(
                quality_totals["unsupported_findings"]
                / quality_totals["total_findings"],
                4,
            )
            if quality_totals["total_findings"]
            else None,
            "uncited_rate": round(
                quality_totals["uncited_findings"]
                / quality_totals["total_findings"],
                4,
            )
            if quality_totals["total_findings"]
            else None,
            "confirmed_precision": (
                round(
                    tier_totals["confirmed"]["tp"]
                    / (
                        tier_totals["confirmed"]["tp"]
                        + tier_totals["confirmed"]["fp"]
                    ),
                    4,
                )
                if tier_totals.get("confirmed", {}).get("tp", 0)
                + tier_totals.get("confirmed", {}).get("fp", 0)
                else None
            ),
            "advisory_precision": (
                round(
                    tier_totals["advisory"]["tp"]
                    / (
                        tier_totals["advisory"]["tp"]
                        + tier_totals["advisory"]["fp"]
                    ),
                    4,
                )
                if tier_totals.get("advisory", {}).get("tp", 0)
                + tier_totals.get("advisory", {}).get("fp", 0)
                else None
            ),
        },
    }


def _ci_text(results: dict, metric: str) -> str:
    item = (results.get("overall_ci") or {}).get(metric)
    if not item:
        return ""
    return f"（95% CI {item['low']:.1%}–{item['high']:.1%}）"


def write_report(results: dict, path: Path) -> None:
    lines = [
        f"# ContractGuard 评测报告（{results['mode']}）",
        "",
        f"- 运行时间：{results['generated_at']}",
        f"- 运行模式：{results['mode']}（模型：{results['model']}）",
        f"- 数据集：8 份合成采购合同，{results['dataset']['labels']} 个标注风险点",
        "- 说明：合成数据、小样本，指标不代表真实泛化能力；规则标签按 rule_id + 条款匹配，语义标签按类别 + 条款 + 关键词匹配。",
    ]
    provenance = results.get("provenance") or {}
    if provenance:
        git = provenance.get("git") or {}
        prompt = provenance.get("prompt") or {}
        kb = provenance.get("kb") or {}
        dataset = provenance.get("dataset") or {}
        command = provenance.get("command") or {}
        lines.extend(
            [
                "",
                "## 复现信息",
                "",
                f"- 代码：{git.get('commit') or 'unknown'}（dirty: {git.get('dirty')}）",
                f"- Prompt：{prompt.get('version') or 'unknown'} / "
                f"{(prompt.get('file_sha256') or '')[:12]}",
                f"- 知识库：{kb.get('version') or 'unknown'} "
                f"（{kb.get('legal_articles', '?')} 法条 / {kb.get('rules', '?')} 规则）",
                f"- 数据集：{dataset.get('id') or 'unknown'} "
                f"{dataset.get('version') or 'unknown'}；"
                f"labels {dataset.get('label_version') or 'unknown'}",
                f"- 哈希：samples {(dataset.get('samples_sha256') or '')[:12]}；"
                f"labels {(dataset.get('labels_sha256') or '')[:12]}"
                f"{'（子集）' if dataset.get('limited') else ''}",
                f"- 命令：`{command.get('entrypoint') or 'eval/run_eval.py'} "
                f"{' '.join(command.get('argv') or [])}`（路径已脱敏）",
            ]
        )
    lines.extend(
        [
            "",
            "## 总体指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| Precision | {results['overall']['precision']:.1%} {_ci_text(results, 'precision')} |",
        f"| Recall | {results['overall']['recall']:.1%} {_ci_text(results, 'recall')} |",
        f"| F1 | {results['overall']['f1']:.1%} {_ci_text(results, 'f1')} |",
        f"| TP / FP / FN | {results['overall']['tp']} / {results['overall']['fp']} / {results['overall']['fn']} |",
        f"| 证据定位率（{results['overall']['evidence_checked']} 条） | {results['overall']['evidence_valid_rate'] or 0:.1%} |",
        f"| 法条 ID 可解析率（{results['overall']['citation_checked']} 个引用） | {results['overall']['citation_valid_rate'] or 0:.1%} |",
        f"| 平均耗时 | {results['average_duration_s']:.2f}s / 份 |",
        "",
            "## 分合同",
            "",
            "| 合同 | 标签 | 命中 | 误报 | 漏报 | P | R | F1 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in results["samples"]:
        score = item["score"]
        lines.append(
            f"| {item['sample_id']} | {item['label_count']} | {score['tp']} | "
            f"{score['fp']} | {score['fn']} | {score['precision']:.1%} | "
            f"{score['recall']:.1%} | {score['f1']:.1%} |"
        )
    lines.extend(["", "## 分层指标", "", "| 等级 | TP | FP | FN | P | R | F1 |", "|---|---|---|---|---|---|---|"])
    for severity in SEVERITIES:
        stats = results["overall_by_severity"][severity]
        lines.append(
            f"| {severity} | {stats['tp']} | {stats['fp']} | {stats['fn']} | "
            f"{stats['precision']:.1%} | {stats['recall']:.1%} | {stats['f1']:.1%} |"
        )

    lines.extend(["", "## 按来源", "", "| 来源 | TP | FP | Precision |", "|---|---|---|---|"])
    for source, stats in results["overall"]["by_source"].items():
        lines.append(
            f"| {source} | {stats['tp']} | {stats['fp']} | {stats['precision']:.1%} |"
        )

    lines.extend(
        ["", "## 按置信分层", "", "| 分层 | TP | FP | Precision |", "|---|---|---|---|"]
    )
    for tier, stats in results["overall"]["by_confidence"].items():
        lines.append(
            f"| {tier} | {stats['tp']} | {stats['fp']} | {stats['precision']:.1%} |"
        )

    quality = results["overall"].get("quality") or {}

    def quality_pct(key: str) -> str:
        value = quality.get(key)
        return "—" if value is None else f"{value:.1%}"

    lines.extend(
        [
            "",
            "## 质量信号",
            "",
            "| 指标 | 值 |",
            "|---|---|",
            f"| 高风险 FP / FN | {quality.get('high_risk_fp', 0)} / {quality.get('high_risk_fn', 0)} |",
            f"| 模型来源无定位证据 | {quality.get('unsupported_findings', 0)} "
            f"（{quality_pct('unsupported_rate')}） |",
            f"| 模型来源无法条引用 | {quality.get('uncited_findings', 0)} "
            f"（{quality_pct('uncited_rate')}） |",
            f"| 已核实层 Precision | {quality_pct('confirmed_precision')} |",
            f"| 模型建议层 Precision | {quality_pct('advisory_precision')} |",
            "",
            "> 这些都是确定性代理指标，不是法律正确性结论；法条相关性、幻觉率和建议可用性需要独立人工评分（见 `eval/run_quality_eval.py`）。",
        ]
    )

    attribution = results["overall"]["fp_attribution"]
    lines.extend(["", "## 误报归因", "", f"- 误报总数：{attribution['total']}"])
    for dimension, label in (
        ("by_source", "来源"),
        ("by_severity", "等级"),
        ("by_category", "类别"),
        ("by_agent", "Agent"),
    ):
        counts = attribution[dimension]
        if counts:
            detail = "、".join(f"{key} {value}" for key, value in counts.items())
            lines.append(f"- 按{label}：{detail}")
    lines.extend(
        [
            f"- 带 rule_hint：{attribution['with_rule_hint']}；不带 rule_hint：{attribution['without_rule_hint']}",
            f"- 证据可定位：{attribution['evidence_located']}；证据缺失：{attribution['evidence_missing']}",
            f"- 有法条引用：{attribution['with_legal_basis']}；无法条引用：{attribution['without_legal_basis']}",
        ]
    )

    lines.extend(
        [
            "",
            "## 局限",
            "",
            f"- {results['dataset']['samples']} 份合同、{results['dataset']['labels']} 个标注点，"
            f"其中 {results['dataset']['rule_labels']} 个是规则可检出点，"
            "规则基线接近满分是构造使然，不代表真实泛化。",
            f"- {results['dataset']['semantic_labels']} 个语义标签用于观察 LLM 是否能补充规则盲区；"
            "full 模式的结果只有在实际运行 live 后才写入本报告。",
            "- 法条 ID 可解析率只衡量引用 ID 是否存在于知识库，不衡量法条与风险的语义相关性。",
            "- 证据定位率的分母只包含提供了原文引用的风险，衡量能否逐字定位，不衡量分析质量。",
            "- 带 rule_hint 不等于规则引擎实际命中：rule_hint 是模型自报提示，不能当作规则佐证。",
            "- 数据、标签和规则由同一作者构造，存在设计偏差；真实业务需独立标注与盲测。",
            "- 95% CI 使用按合同 bootstrap（2000 次、seed=42）；样本仅 8 份，区间很宽，不能解释为统计显著性。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _enforce_thresholds(args: argparse.Namespace, overall: dict) -> None:
    enforce(
        [
            check_min("precision", overall.get("precision"), args.min_precision),
            check_min("recall", overall.get("recall"), args.min_recall),
            check_min("f1", overall.get("f1"), args.min_f1),
        ]
    )


async def main_async(args: argparse.Namespace) -> None:
    if args.from_results:
        suffix = args.from_results
        result_path = BACKEND / "eval" / f"results_{suffix}.json"
        report_path = BACKEND / "eval" / f"report_{suffix}.md"
        results = json.loads(result_path.read_text(encoding="utf-8"))
        results["overall_ci"] = bootstrap_prf(
            [item["score"] for item in results["samples"]]
        )
        results["stats_recomputed_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        results.setdefault("reruns", []).append(
            {
                "at": results["stats_recomputed_at"],
                "command": command_provenance(
                    "eval/run_eval.py",
                    ["--from-results", suffix],
                ),
            }
        )
        if "provenance" not in results:
            results["provenance"] = {
                "schema_version": SCHEMA_VERSION,
                "status": "legacy_missing_provenance",
                "note": "原结果未记录 provenance；本次仅重算 CI。",
            }
        result_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_report(results, report_path)
        print(f"已重算 {suffix} 的 bootstrap CI 并写入 {result_path} / {report_path}")
        _enforce_thresholds(args, results["overall"])
        return

    runtime = chat_runtime(settings)
    route = None
    if args.mode == "full":
        if runtime.mode != "live" and not args.allow_mock:
            print(
                "full 模式需要可用的模型路由。若只想验证流水线，"
                "请加 --allow-mock（结果不代表 LLM 能力）。"
            )
            raise SystemExit(2)
        try:
            route = resolve_route(
                args.route, require_available=runtime.mode == "live"
            )
        except RouteError as exc:
            print(f"模型路由不可用：{exc}")
            raise SystemExit(2) from exc

    samples = yaml.safe_load((SAMPLES_DIR / "samples.yaml").read_text(encoding="utf-8"))
    if args.limit:
        samples = samples[: args.limit]

    sample_results = []
    durations = []
    rule_label_count = 0
    semantic_label_count = 0
    for sample in samples:
        labels = load_labels(sample["id"])
        rule_label_count += sum(1 for label in labels if label.rule_id)
        semantic_label_count += sum(1 for label in labels if not label.rule_id)
        if args.mode == "rules":
            findings, duration = rules_run(sample, labels)
            usage = None
        else:
            findings, duration, usage = await full_run(
                sample,
                labels,
                route.id if route else args.route,
                runtime.mode,
                disable_rag=args.disable_rag,
            )
        score_data = score(labels, findings)
        durations.append(duration)
        citation_count = score_data["citation_checked"]
        valid_citation_count = score_data["citation_valid"]
        evidence_checked = score_data["evidence_checked"]
        evidence_valid = score_data["evidence_valid"]
        sample_results.append(
            {
                "sample_id": sample["id"],
                "title": sample["title"],
                "label_count": len(labels),
                "finding_count": len(findings),
                "score": score_data,
                "duration_s": round(duration, 2),
                "usage": usage,
                "citation_count": citation_count,
                "valid_citation_count": valid_citation_count,
                "evidence_checked": evidence_checked,
                "evidence_valid": evidence_valid,
            }
        )
        print(
            f"{sample['id']}: TP={score_data['tp']} FP={score_data['fp']} "
            f"FN={score_data['fn']} P={score_data['precision']:.1%} "
            f"R={score_data['recall']:.1%} ({duration:.2f}s)"
        )

    overall = aggregate(sample_results)
    overall_by_severity = {
        severity: {"tp": 0, "fp": 0, "fn": 0, "precision": 0, "recall": 0, "f1": 0}
        for severity in SEVERITIES
    }
    for item in sample_results:
        for severity in SEVERITIES:
            for key in ("tp", "fp", "fn"):
                overall_by_severity[severity][key] += item["score"]["by_severity"][severity][key]
    for severity, stats in overall_by_severity.items():
        precision = stats["tp"] / (stats["tp"] + stats["fp"]) if stats["tp"] + stats["fp"] else 0
        recall = stats["tp"] / (stats["tp"] + stats["fn"]) if stats["tp"] + stats["fn"] else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        stats.update(
            {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
            }
        )

    usages = [item["usage"] for item in sample_results if item.get("usage")]
    used_models = sorted(
        {model for usage in usages for model in (usage.get("llm_models") or [])}
    )
    route_labels = sorted(
        {
            usage.get("llm_route_label")
            for usage in usages
            if usage.get("llm_route_label")
        }
    )
    model_label = (
        " / ".join(used_models)
        or " / ".join(route_labels)
        or ("mock/rules-only" if runtime.mode != "live" else "unknown")
    )
    dataset_prov = synthetic_dataset_provenance(
        samples,
        SAMPLES_DIR,
        LABELS_DIR,
        load_kb(),
        limited=bool(args.limit),
    )
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "kind": "synthetic_eval",
        "git": git_provenance(),
        "prompt": prompt_provenance(),
        "profiles": profiles_provenance(),
        "kb": {
            "version": dataset_prov["kb_version"],
            "sha256": dataset_prov["kb_sha256"],
            "legal_articles": dataset_prov["legal_articles"],
            "rules": dataset_prov["rules"],
        },
        "dataset": dataset_prov,
        "runtime": {
            "eval_mode": args.mode,
            "chat_runtime_mode": runtime.mode,
            "route": route.id if route else args.route,
            "route_label": route.label if route else None,
            "models": used_models,
        },
        "command": command_provenance("eval/run_eval.py", sys.argv[1:]),
        "app_version": APP_VERSION,
        "python_version": python_version(),
    }

    results = {
        "mode": args.mode,
        "mock": runtime.mode != "live",
        "model": model_label,
        "llm_route": route.id if route else args.route,
        "llm_route_label": route.label if route else None,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ablation": {"disable_rag": bool(args.disable_rag)},
        "dataset": {
            "id": dataset_prov["id"],
            "version": dataset_prov["version"],
            "label_version": dataset_prov["label_version"],
            "split": dataset_prov["split"],
            "samples_sha256": dataset_prov["samples_sha256"],
            "labels_sha256": dataset_prov["labels_sha256"],
            "limited": dataset_prov["limited"],
            "samples": len(samples),
            "labels": sum(item["label_count"] for item in sample_results),
            "rule_labels": rule_label_count,
            "semantic_labels": semantic_label_count,
            "note": (
                f"{len(samples)} synthetic procurement contracts; "
                f"{rule_label_count} rule-detectable labels + "
                f"{semantic_label_count} semantic labels"
            ),
        },
        "provenance": provenance,
        "overall": overall,
        "overall_ci": bootstrap_prf(
            [item["score"] for item in sample_results]
        ),
        "overall_by_severity": overall_by_severity,
        "average_duration_s": round(sum(durations) / len(durations), 2) if durations else 0,
        "samples": sample_results,
    }
    suffix = args.output_suffix or (
        "full_mock"
        if args.mode == "full" and runtime.mode != "live"
        else args.mode
    )
    output_json = BACKEND / "eval" / f"results_{suffix}.json"
    output_md = BACKEND / "eval" / f"report_{suffix}.md"
    output_json.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(results, output_md)
    print("\n总体：", json.dumps(overall, ensure_ascii=False))
    print(f"写入 {output_json} 和 {output_md}")
    _enforce_thresholds(args, overall)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rules", "full"], default="rules")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--route", default=None, help="LLM 路由 ID")
    parser.add_argument(
        "--disable-rag",
        action="store_true",
        help="消融用：不把检索到的法条/Playbook 放进 prompt",
    )
    parser.add_argument(
        "--output-suffix",
        default=None,
        help="自定义结果/报告后缀，例如 full_no_rag（不覆盖默认 full 结果）",
    )
    parser.add_argument("--min-precision", type=float, default=None)
    parser.add_argument("--min-recall", type=float, default=None)
    parser.add_argument("--min-f1", type=float, default=None)
    parser.add_argument(
        "--from-results",
        choices=["rules", "full"],
        default=None,
        help="不重新运行审查，只从已落盘结果重算 bootstrap CI 并重写报告",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
