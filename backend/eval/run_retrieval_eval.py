"""检索评测：BM25 / hybrid / BM25+rerank / hybrid+rerank。

用法（backend 目录）：
    uv run python eval/run_retrieval_eval.py
    uv run python eval/run_retrieval_eval.py --limit 10
    uv run python eval/run_retrieval_eval.py --no-rerank   # 只跑 BM25 + hybrid

query 集是作者构造的 25 条场景，不是独立法务标注；结果只做小样本
描述性对比，不能代表大样本泛化。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.rag.corpus import load_kb  # noqa: E402
from app.rag.retriever import HybridRetriever  # noqa: E402
from eval.stats import bootstrap_metric, ndcg_at_k  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
QUERIES_PATH = BACKEND / "eval" / "retrieval" / "queries.yaml"
RESULTS_PATH = BACKEND / "eval" / "results_retrieval.json"
REPORT_PATH = BACKEND / "eval" / "report_retrieval.md"


def compute_metrics(
    ranked: list[str], relevant: list[str], k: int
) -> dict[str, float]:
    relevant_set = set(relevant)
    top = ranked[:k]
    hits = [doc_id for doc_id in top if doc_id in relevant_set]
    hit = 1.0 if hits else 0.0
    recall = len(set(hits)) / len(relevant_set) if relevant_set else 0.0
    mrr = 0.0
    for index, doc_id in enumerate(top):
        if doc_id in relevant_set:
            mrr = 1.0 / (index + 1)
            break
    return {
        "hit": round(hit, 4),
        "recall": round(recall, 4),
        "mrr": round(mrr, 4),
        "ndcg": round(ndcg_at_k(ranked, relevant, k), 4),
    }


def average_metrics(items: list[dict[str, float]]) -> dict[str, float]:
    if not items:
        return {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    keys = ("hit", "recall", "mrr", "ndcg")
    return {
        key: round(
            sum(item.get(key, 0.0) for item in items) / len(items), 4
        )
        for key in keys
    }


def load_queries(limit: int = 0) -> list[dict]:
    queries = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))
    if limit:
        queries = queries[:limit]
    return queries


def _configs(include_rerank: bool) -> list[tuple[str, object]]:
    configs: list[tuple[str, object]] = [
        ("bm25", replace(settings, llm_mode="mock", rerank_enabled=False)),
        ("hybrid", replace(settings, rerank_enabled=False)),
    ]
    if include_rerank:
        configs.extend(
            [
                ("bm25_rerank", replace(settings, llm_mode="mock", rerank_enabled=True)),
                ("hybrid_rerank", replace(settings, rerank_enabled=True)),
            ]
        )
    return configs


def aggregate_queries(per_query: list[dict]) -> tuple[dict, dict, dict]:
    metrics = ("hit", "recall", "mrr", "ndcg")
    overall = {
        f"{metric}@{k}": average_metrics(
            [item[f"metrics@{k}"] for item in per_query]
        )[metric]
        for k in (5, 10)
        for metric in metrics
    }
    overall_ci = {
        metric: bootstrap_metric(
            [item["metrics@10"][metric] for item in per_query],
            seed=42,
        )
        for metric in metrics
    }
    by_source: dict[str, dict] = {}
    for source in sorted({item["source"] for item in per_query}):
        subset = [item for item in per_query if item["source"] == source]
        by_source[source] = {
            "queries": len(subset),
            **{
                f"{metric}@10": average_metrics(
                    [item["metrics@10"] for item in subset]
                )[metric]
                for metric in metrics
            },
        }
    return overall, overall_ci, by_source


async def run_config(
    name: str,
    config,
    queries: list[dict],
) -> dict:
    retriever = HybridRetriever(load_kb(), config)
    if config.llm_mode == "live" and retriever.embeddings.available:
        await retriever.warmup()

    per_query = []
    for item in queries:
        results = await retriever.retrieve(
            item["query"], top_k=10, doc_types=["law"]
        )
        ranked = [result.document.id for result in results]
        relevant = list(item.get("relevant") or [])
        per_query.append(
            {
                "id": item["id"],
                "query": item["query"],
                "source": item.get("source", "author"),
                "category": item.get("category", "general"),
                "relevant": relevant,
                "ranked": ranked,
                "metrics@5": compute_metrics(ranked, relevant, 5),
                "metrics@10": compute_metrics(ranked, relevant, 10),
            }
        )

    overall, overall_ci, by_source = aggregate_queries(per_query)
    return {
        "name": name,
        "retrieval_mode": retriever.retrieval_mode,
        "rerank_mode": retriever.rerank_mode,
        "last_warning": retriever.last_warning,
        "overall": overall,
        "overall_ci": overall_ci,
        "by_source": by_source,
        "queries": per_query,
    }


def _ci_cell(config: dict, metric: str) -> str:
    item = (config.get("overall_ci") or {}).get(metric)
    if not item:
        return "—"
    return f"{item['point']:.1%} ({item['low']:.1%}–{item['high']:.1%})"


def write_report(results: dict, queries: list[dict]) -> None:
    lines = [
        "# ContractGuard 检索评测报告",
        "",
        f"- 运行时间：{results['generated_at']}",
        f"- query 数：{len(queries)}；语料：32 条法条",
        "- 口径：作者构造的 25 条 query→relevant law 场景；不是独立法务标注，不能代表大样本泛化。",
        "- 指标：Hit@k / Recall@k / MRR@k / nDCG@k；微平均按 query 等权。",
        "",
        "## 总体",
        "",
        "| 配置 | 检索模式 | 重排模式 | Hit@5 | Recall@5 | MRR@5 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for config in results["configs"]:
        overall = config["overall"]
        lines.append(
            f"| {config['name']} | {config['retrieval_mode']} | {config['rerank_mode']} | "
            f"{overall['hit@5']:.1%} | {overall['recall@5']:.1%} | {overall['mrr@5']:.3f} | "
            f"{overall['hit@10']:.1%} | {overall['recall@10']:.1%} | {overall['mrr@10']:.3f} | "
            f"{overall.get('ndcg@10', 0):.3f} |"
        )

    lines.extend(
        [
            "",
            "## 95% CI（按 query bootstrap，2000 次，seed=42）",
            "",
            "| 配置 | Recall@10 | MRR@10 | nDCG@10 |",
            "|---|---|---|---|",
        ]
    )
    for config in results["configs"]:
        lines.append(
            f"| {config['name']} | {_ci_cell(config, 'recall')} | "
            f"{_ci_cell(config, 'mrr')} | {_ci_cell(config, 'ndcg')} |"
        )

    lines.extend(
        [
            "",
            "## 按来源（Recall@10 / MRR@10 / nDCG@10）",
            "",
            "| 配置 | 来源 | 数量 | Recall@10 | MRR@10 | nDCG@10 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for config in results["configs"]:
        for source, stats in config["by_source"].items():
            lines.append(
                f"| {config['name']} | {source} | {stats['queries']} | "
                f"{stats['recall@10']:.1%} | {stats['mrr@10']:.3f} | "
                f"{stats.get('ndcg@10', 0):.3f} |"
            )

    lines.extend(
        [
            "",
            "## 运行提示",
            "",
        ]
    )
    for config in results["configs"]:
        if config.get("last_warning"):
            lines.append(f"- {config['name']}：{config['last_warning']}")
    if not any(config.get("last_warning") for config in results["configs"]):
        lines.append("- 无")

    lines.extend(
        [
            "",
            "## 局限",
            "",
            "- 25 条 query 由作者构造，relevant 集合来自 Playbook/人工判断，存在构造偏差。",
            "- 语料只有 32 条法条，召回空间很小；不能外推到完整法律数据库。",
            "- 95% CI 是按 query bootstrap 的描述性区间；没有独立法务标注、盲测或显著性检验。",
            "- rerank 模型/接口可能随 DashScope 下线而变化；失败时会回退 RRF，报告中的 rerank 行可能等同 BM25/RRF。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    if args.from_results:
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        for config in results["configs"]:
            for item in config["queries"]:
                item["metrics@5"] = compute_metrics(
                    item["ranked"], item["relevant"], 5
                )
                item["metrics@10"] = compute_metrics(
                    item["ranked"], item["relevant"], 10
                )
            overall, overall_ci, by_source = aggregate_queries(config["queries"])
            config["overall"] = overall
            config["overall_ci"] = overall_ci
            config["by_source"] = by_source
        results["stats_recomputed_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        RESULTS_PATH.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_report(results, results["configs"][0]["queries"])
        print(f"已重算 nDCG/CI 并写入 {RESULTS_PATH} 和 {REPORT_PATH}")
        return

    queries = load_queries(args.limit)
    configs = _configs(include_rerank=not args.no_rerank)
    results = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query_count": len(queries),
        "configs": [],
    }
    for name, config in configs:
        print(f"running {name}...")
        result = await run_config(name, config, queries)
        results["configs"].append(result)
        print(
            f"{name}: retrieval={result['retrieval_mode']} "
            f"rerank={result['rerank_mode']} "
            f"Recall@10={result['overall']['recall@10']:.1%}"
        )
    RESULTS_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(results, queries)
    print(f"写入 {RESULTS_PATH} 和 {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument(
        "--from-results",
        action="store_true",
        help="不重新检索，只从已落盘结果重算 nDCG/CI 并重写报告",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
