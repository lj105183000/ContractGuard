"""Summarize token usage, latency and retry overhead from a full eval run.

Prices are explicit assumptions, not a bill. Always check the provider's
current pricing page before quoting cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.stats import percentile  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SOURCE_PATH = BACKEND / "eval" / "results_full.json"
JSON_PATH = BACKEND / "eval" / "results_cost.json"
REPORT_PATH = BACKEND / "eval" / "report_cost.md"


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    input_price_per_1k: float,
    output_price_per_1k: float,
) -> float:
    return round(
        input_tokens / 1000 * input_price_per_1k
        + output_tokens / 1000 * output_price_per_1k,
        4,
    )


def summarize(
    payload: dict,
    input_price_per_1k: float,
    output_price_per_1k: float,
) -> dict:
    durations = [float(item["duration_s"]) for item in payload["samples"]]
    calls = 0
    input_tokens = 0
    output_tokens = 0
    traces = []
    per_contract = []
    for item in payload["samples"]:
        usage = item.get("usage") or {}
        calls += int(usage.get("llm_calls", 0))
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
        sample_traces = usage.get("traces") or []
        traces.extend(sample_traces)
        per_contract.append(
            {
                "sample_id": item["sample_id"],
                "duration_s": item["duration_s"],
                "llm_calls": usage.get("llm_calls", 0),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "failed_calls": sum(1 for trace in sample_traces if not trace.get("success")),
                "cost_estimate": estimate_cost(
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                    input_price_per_1k,
                    output_price_per_1k,
                ),
            }
        )

    failed = [trace for trace in traces if not trace.get("success")]
    rate_limited = [
        trace for trace in failed if "429" in str(trace.get("error") or "")
    ]
    model_counts: dict[str, int] = {}
    failed_by_model: dict[str, int] = {}
    for trace in traces:
        model = trace.get("model") or "unknown"
        model_counts[model] = model_counts.get(model, 0) + 1
    for trace in failed:
        model = trace.get("model") or "unknown"
        failed_by_model[model] = failed_by_model.get(model, 0) + 1

    total_cost = estimate_cost(
        input_tokens, output_tokens, input_price_per_1k, output_price_per_1k
    )
    return {
        "source": SOURCE_PATH.name,
        "mode": payload.get("mode"),
        "model": payload.get("model"),
        "generated_at": payload.get("generated_at"),
        "samples": len(payload["samples"]),
        "calls": calls,
        "failed_calls": len(failed),
        "rate_limited_calls": len(rate_limited),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_price_per_1k": input_price_per_1k,
        "output_price_per_1k": output_price_per_1k,
        "currency": "CNY",
        "cost_estimate": total_cost,
        "latency_s": {
            "mean": round(sum(durations) / len(durations), 2),
            "p50": round(percentile(durations, 0.50), 2),
            "p95": round(percentile(durations, 0.95), 2),
            "min": round(min(durations), 2),
            "max": round(max(durations), 2),
        },
        "model_counts": dict(sorted(model_counts.items())),
        "failed_by_model": dict(sorted(failed_by_model.items())),
        "per_contract": per_contract,
    }


def write_report(summary: dict) -> None:
    latency = summary["latency_s"]
    lines = [
        "# ContractGuard 成本与延迟摘要",
        "",
        f"- 来源：`eval/{summary['source']}`（{summary['model']}，{summary['mode']}）",
        f"- 合同数：{summary['samples']}；LLM 调用：{summary['calls']} 次"
        f"（失败 {summary['failed_calls']}，其中 429 {summary['rate_limited_calls']}）",
        f"- Token：输入 {summary['input_tokens']:,} / 输出 {summary['output_tokens']:,}",
        f"- 延迟：mean {latency['mean']}s，p50 {latency['p50']}s，p95 {latency['p95']}s，"
        f"min {latency['min']}s，max {latency['max']}s",
        "",
        "## 估算费用",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 假设输入单价 | ¥{summary['input_price_per_1k']}/1K tokens |",
        f"| 假设输出单价 | ¥{summary['output_price_per_1k']}/1K tokens |",
        f"| 估算总费用 | **¥{summary['cost_estimate']:.4f}**（{summary['samples']} 份） |",
        f"| 单份均价 | ¥{summary['cost_estimate'] / summary['samples']:.4f} |",
        "",
        "> 单价是脚本默认假设，不是百炼账单，也不含 embedding/rerank/网络重试成本；"
        "正式引用前用当期价格重新运行 `--input-price-per-1k/--output-price-per-1k`。",
        "",
        "## 分合同",
        "",
        "| 合同 | 耗时(s) | LLM 调用 | 失败 | 输入 tokens | 输出 tokens | 估算费用(¥) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["per_contract"]:
        lines.append(
            f"| {item['sample_id']} | {item['duration_s']} | {item['llm_calls']} | "
            f"{item['failed_calls']} | {item['input_tokens']:,} | "
            f"{item['output_tokens']:,} | {item['cost_estimate']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 模型与失败归因",
            "",
            f"- 调用模型分布：{summary['model_counts']}",
            f"- 失败分布：{summary['failed_by_model']}",
            "- 失败调用多来自 qwen-turbo 的 429/请求限流，随后回退到 qwen-plus；"
            "失败调用不计 token，但会增加延迟和不确定性。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-price-per-1k", type=float, default=0.0008)
    parser.add_argument("--output-price-per-1k", type=float, default=0.002)
    args = parser.parse_args()
    payload = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    summary = summarize(
        payload, args.input_price_per_1k, args.output_price_per_1k
    )
    JSON_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(summary)
    print(f"写入 {JSON_PATH} 和 {REPORT_PATH}")
    print(
        f"calls={summary['calls']} failed={summary['failed_calls']} "
        f"cost={summary['cost_estimate']:.4f} CNY "
        f"p95={summary['latency_s']['p95']}s"
    )


if __name__ == "__main__":
    main()
