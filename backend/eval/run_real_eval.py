"""真实合同外部效度检查（作者标注，非盲测）。

真实 PDF 和完整标签不随公共仓库分发。用法（backend 目录）：
    uv run python eval/run_real_eval.py \
      --contracts-dir ../contractguard-private/docs \
      --manifest ../contractguard-private/real_eval/manifest.yaml \
      --labels-dir ../contractguard-private/real_eval/labels \
      --raw-dir ../contractguard-private/real_eval/raw

默认 rules 模式（无需 Key）；--mode full 才会调用 live LLM。
输出只含匿名 ID、计数和指标，不含合同全文/文件名。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.build import build_graph  # noqa: E402
from app.llm.trace import TraceCollector  # noqa: E402
from app.parsing.clause_splitter import split_clauses  # noqa: E402
from app.parsing.contract_parser import parse_contract  # noqa: E402
from app.parsing.facts import extract_heuristic_facts  # noqa: E402
from app.rag.corpus import load_kb  # noqa: E402
from app.rules.engine import evaluate_rules  # noqa: E402
from app.schemas import EvalLabel  # noqa: E402
from eval.run_eval import score  # noqa: E402
from eval.stats import bootstrap_prf  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SUMMARY_PATH = BACKEND / "eval" / "results_real_summary.json"
REPORT_PATH = BACKEND / "eval" / "report_real.md"


def load_labels(path: Path) -> list[EvalLabel]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalLabel.model_validate(item) for item in data]


def rule_findings(parsed, clauses, facts):
    kb = load_kb()
    return evaluate_rules(kb.rules, clauses, facts, parsed.text).findings


async def full_findings(path: Path, filename: str):
    graph = build_graph()
    state = {
        "job_id": f"real-eval-{path.stem[:20]}",
        "filename": filename,
        "source_path": str(path),
        "llm_mode": "live",
        "trace": TraceCollector(),
    }
    result = await graph.ainvoke(state, config={"recursion_limit": 30})
    report = result.get("report")
    return report.findings if report is not None else []


def summarize_score(score_data: dict | None) -> dict | None:
    if score_data is None:
        return None
    return {
        key: score_data.get(key)
        for key in (
            "tp",
            "fp",
            "fn",
            "precision",
            "recall",
            "f1",
            "evidence_valid_rate",
            "citation_valid_rate",
            "evidence_checked",
            "citation_checked",
        )
    }


def write_report(
    rows: list[dict], mode: str, bootstrap_ci: dict[str, dict]
) -> None:
    lines = [
        "# ContractGuard 真实合同外部效度检查",
        "",
        f"- 运行时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 模式：{mode}（rules 模式不调用 LLM；full 模式会调用 live 模型）",
        "- 定位：**作者标注、非独立、非盲测**；不是 benchmark，不能代表真实泛化。",
        "- 数据：3 份真实合同，只有 R1 属于货物采购目标场景；R3 相邻，R2 域外。",
        "- 公开报告只含 R1/R2/R3 匿名 ID、计数和指标；不含合同全文、文件名或个人信息。",
        "",
        "## 总览",
        "",
        "| 合同 | 场景 | 页数 | OCR | 条款数 | 规则/模型 findings | 标注数 | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        metrics = row.get("score") or {}
        suffix = "" if row.get("scope") == "in_scope" else "（仅定性）"
        lines.append(
            f"| {row['title']} | {row['scope']} | {row['pages']} | "
            f"{'是' if row['used_ocr'] else '否'} | {row['clauses']} | "
            f"{row['findings']} | {row['labels']} | "
            f"{metrics.get('precision', '—')} | {metrics.get('recall', '—')} | "
            f"{metrics.get('f1', '—')}{suffix} |"
        )

    r1 = next((row for row in rows if row["scope"] == "in_scope"), None)
    if r1 and r1.get("score"):
        metrics = r1["score"]
        ci = bootstrap_ci.get(r1["id"]) or {}

        def ci_text(metric: str) -> str:
            item = ci.get(metric)
            if not item:
                return "—"
            return f"{item['low']:.1%}–{item['high']:.1%}"

        lines.extend(
            [
                "",
                "## R1 作者标注指标（in_scope）",
                "",
                "| 指标 | 值 | 95% CI |",
                "|---|---|---|",
                f"| TP / FP / FN | {metrics['tp']} / {metrics['fp']} / {metrics['fn']} | — |",
                f"| Precision | {metrics['precision']:.1%} | {ci_text('precision')} |",
                f"| Recall | {metrics['recall']:.1%} | {ci_text('recall')} |",
                f"| F1 | {metrics['f1']:.1%} | {ci_text('f1')} |",
                f"| 证据定位率（{metrics['evidence_checked']} 条） | "
                f"{metrics['evidence_valid_rate']:.1%} | — |",
                f"| 法条 ID 可解析率（{metrics['citation_checked']} 个引用） | "
                f"{metrics['citation_valid_rate']:.1%} | — |",
                "",
                "> R1 只有 1 份 in-scope 合同，按合同 bootstrap 的 CI 退化为点估计；"
                "它只表示样本量不足，不代表精度确定。",
            ]
        )

    lines.extend(
        [
            "",
            "## 运行与解析观察",
            "",
            "- R1 有文字层；R2/R3 是扫描件，规则模式只做本地 OCR，不调用 LLM。",
            "- 真实 PDF 的条款切分出现金额/编号被识别成 article 的噪声；因此真实标签使用 "
            "`clause_id`/`quote` 锚定，不能用 `clause_article` 精确匹配。",
            "- R1 的规则 finding 中存在证据挂错条款的情况（例如 IP 规则引用了质保条款）；"
            "这是真实合同解析/规则证据绑定的已知问题，报告如实计入 FP/FN，不做人工修饰。",
            "- R2 施工合同属于域外，规则命中低不能解释为模型能力；R3 IT 采购属于相邻场景，"
            "均不与 R1 混合计算微平均。",
            "",
            "## 不能据此声称",
            "",
            "- 真实泛化能力、法务准确率、盲测结果、inter-annotator agreement、统计显著性。",
            "- 作者不是法律专业人士；标注受作者规则设计影响；样本只有 3 份（1 份 in-scope）。",
            "- 真实合同/完整标签不随仓库分发；本报告只含匿名 ID 和计数。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    manifest = yaml.safe_load(
        Path(args.manifest).read_text(encoding="utf-8")
    )
    if args.limit:
        manifest = manifest[: args.limit]
    contracts_dir = Path(args.contracts_dir)
    labels_dir = Path(args.labels_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else None
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for item in manifest:
        path = contracts_dir / item["filename"]
        parsed = parse_contract(path, item["filename"])
        clauses = split_clauses(parsed)
        facts = extract_heuristic_facts(parsed.text, clauses)
        if args.mode == "full":
            findings = await full_findings(path, item["filename"])
        else:
            findings = rule_findings(parsed, clauses, facts)
        labels = load_labels(labels_dir / f"{item['id']}.json")
        score_data = score(labels, findings) if labels else None
        row = {
            "id": item["id"],
            "title": item.get("title") or item["id"],
            "scope": item.get("scope", "in_scope"),
            "pages": parsed.page_count,
            "used_ocr": parsed.used_ocr,
            "ocr_pages": parsed.ocr_pages,
            "ocr_failed_pages": parsed.ocr_failed_pages,
            "ocr_confidence": parsed.ocr_confidence,
            "clauses": len(clauses),
            "findings": len(findings),
            "labels": len(labels),
            "score": summarize_score(score_data),
            "notes": item.get("notes", ""),
        }
        rows.append(row)
        if raw_dir:
            raw_path = raw_dir / f"{item['id']}.findings.json"
            raw_path.write_text(
                json.dumps(
                    [finding.model_dump(mode="json") for finding in findings],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        print(
            f"{row['id']}: scope={row['scope']} pages={row['pages']} "
            f"ocr={row['used_ocr']} clauses={row['clauses']} "
            f"findings={row['findings']} labels={row['labels']} "
            f"score={row['score']}"
        )

    bootstrap_ci = {
        row["id"]: bootstrap_prf([row["score"]])
        for row in rows
        if row.get("score")
    }
    results = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": args.mode,
        "annotation_protocol": "author_single_pass",
        "independent_annotator": False,
        "blind": False,
        "system_output_seen_during_annotation": False,
        "inter_annotator_agreement": None,
        "iaa_note": "No second independent annotator is available; use eval/run_iaa.py when labels exist.",
        "bootstrap_ci": bootstrap_ci,
        "contracts": rows,
    }
    SUMMARY_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(rows, args.mode, bootstrap_ci)
    print(f"写入 {SUMMARY_PATH} 和 {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--mode", choices=["rules", "full"], default="rules")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
