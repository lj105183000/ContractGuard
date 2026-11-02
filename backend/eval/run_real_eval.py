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
from app.provenance import (  # noqa: E402
    SCHEMA_VERSION,
    command_provenance,
    git_provenance,
    profiles_provenance,
    prompt_provenance,
    sha256_file,
)
from app.rag.corpus import load_kb  # noqa: E402
from app.rules.engine import evaluate_rules  # noqa: E402
from app.schemas import EvalLabel  # noqa: E402
from eval.provenance import (  # noqa: E402
    kb_fingerprint,
    real_contracts_provenance,
)
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


def load_manifest(path: Path) -> tuple[dict, list[dict]]:
    """Load legacy list or v2 mapping; infer dev split for legacy manifests."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        meta = dict(data.get("dataset") or {})
        contracts = list(data.get("contracts") or [])
    else:
        meta = {"split_inferred": True}
        contracts = list(data or [])
    for item in contracts:
        item.setdefault("split", "dev")
    return meta, contracts


def select_contracts(
    contracts: list[dict], split: str, allow_holdout: bool
) -> list[dict]:
    if split not in {"all", "dev", "holdout"}:
        raise SystemExit(f"非法 split：{split}")
    has_holdout = any(item.get("split", "dev") == "holdout" for item in contracts)
    if split in {"all", "holdout"} and has_holdout and not allow_holdout:
        raise SystemExit(
            "holdout 数据只能用于最终报告；请显式加 --allow-holdout。"
        )
    selected = (
        contracts
        if split == "all"
        else [item for item in contracts if item.get("split", "dev") == split]
    )
    if not selected:
        raise SystemExit(f"split={split} 没有可选合同。")
    return selected


async def full_findings(path: Path, filename: str):
    graph = build_graph()
    trace = TraceCollector()
    state = {
        "job_id": f"real-eval-{path.stem[:20]}",
        "filename": filename,
        "source_path": str(path),
        "llm_mode": "live",
        "trace": trace,
    }
    result = await graph.ainvoke(state, config={"recursion_limit": 30})
    report = result.get("report")
    return (
        report.findings if report is not None else [],
        report.usage if report is not None else None,
    )


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
    rows: list[dict],
    mode: str,
    bootstrap_ci: dict[str, dict],
    *,
    provenance: dict | None = None,
    split: str = "all",
    report_path: Path = REPORT_PATH,
) -> None:
    lines = [
        "# ContractGuard 真实合同外部效度检查",
        "",
        f"- 运行时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 模式：{mode}（rules 模式不调用 LLM；full 模式会调用 live 模型）",
        f"- 数据：{len(rows)} 份真实合同（split={split}）。",
        "- 定位：**作者标注、非独立、非盲测**；不是 benchmark，不能代表真实泛化。",
        "- 公开报告只含匿名 ID、计数和指标；不含合同全文、文件名或个人信息。",
    ]
    if split == "holdout":
        lines.extend(
            [
                "",
                "> **HOLDOUT**：仅用于最终报告，不得用于调规则/Prompt/阈值。",
            ]
        )
    if provenance:
        dataset = provenance.get("dataset") or {}
        git = provenance.get("git") or {}
        prompt = provenance.get("prompt") or {}
        kb = provenance.get("kb") or {}
        command = provenance.get("command") or {}
        lines.extend(
            [
                "",
                "## 复现信息",
                "",
                f"- 代码：{git.get('commit') or 'unknown'}（dirty: {git.get('dirty')}）",
                f"- Prompt：{prompt.get('version') or 'unknown'}",
                f"- 知识库：{kb.get('version') or 'unknown'} "
                f"（{kb.get('legal_articles', '?')} 法条 / {kb.get('rules', '?')} 规则）",
                f"- 数据集：{dataset.get('id') or 'unknown'} "
                f"{dataset.get('version') or 'unknown'}；"
                f"labels {dataset.get('label_version') or 'unknown'}",
                f"- 命令：`{command.get('entrypoint') or 'eval/run_real_eval.py'} "
                f"{' '.join(command.get('argv') or [])}`（路径已脱敏）",
            ]
        )
    lines.extend(
        [
            "",
            "## 总览",
            "",
            "| 合同 | 场景 | 页数 | OCR | 条款数 | 规则/模型 findings | 标注数 | Precision | Recall | F1 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
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
    report_path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    manifest_meta, contracts = load_manifest(Path(args.manifest))
    if args.limit:
        contracts = contracts[: args.limit]
    selected = select_contracts(contracts, args.split, args.allow_holdout)
    contracts_dir = Path(args.contracts_dir)
    labels_dir = Path(args.labels_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else None
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    provenance_contracts = []
    models: set[str] = set()
    route_label = None
    for item in selected:
        path = contracts_dir / item["filename"]
        actual_sha = sha256_file(path)
        expected_sha = item.get("sha256")
        if expected_sha and actual_sha != expected_sha:
            raise SystemExit(f"{item['id']} 文件 SHA-256 不匹配：{path.name}")
        parsed = parse_contract(path, item["filename"])
        clauses = split_clauses(parsed)
        facts = extract_heuristic_facts(parsed.text, clauses)
        if args.mode == "full":
            findings, usage = await full_findings(path, item["filename"])
            if usage is not None:
                models.update(usage.llm_models or [])
                route_label = route_label or usage.llm_route_label
        else:
            findings = rule_findings(parsed, clauses, facts)
        labels = load_labels(labels_dir / f"{item['id']}.json")
        score_data = score(labels, findings) if labels else None
        row = {
            "id": item["id"],
            "title": item.get("title") or item["id"],
            "scope": item.get("scope", "in_scope"),
            "split": item.get("split", "dev"),
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
        provenance_contracts.append(
            {
                "id": item["id"],
                "sha256": actual_sha,
                "has_labels": bool(labels),
            }
        )
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
            f"{row['id']}: scope={row['scope']} split={row['split']} "
            f"pages={row['pages']} ocr={row['used_ocr']} "
            f"clauses={row['clauses']} findings={row['findings']} "
            f"labels={row['labels']} score={row['score']}"
        )

    bootstrap_ci = {
        row["id"]: bootstrap_prf([row["score"]])
        for row in rows
        if row.get("score")
    }
    dataset_prov = real_contracts_provenance(
        provenance_contracts, labels_dir, Path(args.manifest)
    )
    dataset_prov.update(
        {
            "declared_version": manifest_meta.get("dataset_version"),
            "declared_label_version": manifest_meta.get("label_version"),
            "split": args.split,
            "holdout_used": any(
                item.get("split", "dev") == "holdout" for item in selected
            ),
            "split_inferred": bool(manifest_meta.get("split_inferred")),
            "limited": bool(args.limit),
        }
    )
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "kind": "real_eval",
        "git": git_provenance(),
        "prompt": prompt_provenance(),
        "profiles": profiles_provenance(),
        "kb": kb_fingerprint(load_kb()),
        "dataset": dataset_prov,
        "runtime": {
            "mode": args.mode,
            "models": sorted(models),
            "route_label": route_label,
        },
        "command": command_provenance("eval/run_real_eval.py", sys.argv[1:]),
    }
    results = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": args.mode,
        "split_filter": args.split,
        "holdout_used": dataset_prov["holdout_used"],
        "annotation_protocol": "author_single_pass",
        "independent_annotator": False,
        "blind": False,
        "system_output_seen_during_annotation": False,
        "inter_annotator_agreement": None,
        "iaa_note": "No second independent annotator is available; use eval/run_iaa.py when labels exist.",
        "bootstrap_ci": bootstrap_ci,
        "provenance": provenance,
        "contracts": rows,
    }
    suffix = args.output_suffix or ("" if args.split == "all" else f"_{args.split}")
    if any(token in suffix for token in ("/", "\\", "..")):
        raise SystemExit("--output-suffix 不能包含路径分隔符")
    summary_path = BACKEND / "eval" / f"results_real{suffix}_summary.json"
    report_path = BACKEND / "eval" / f"report_real{suffix}.md"
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(
        rows,
        args.mode,
        bootstrap_ci,
        provenance=provenance,
        split=args.split,
        report_path=report_path,
    )
    print(f"写入 {summary_path} 和 {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--mode", choices=["rules", "full"], default="rules")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--output-suffix", default=None)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
