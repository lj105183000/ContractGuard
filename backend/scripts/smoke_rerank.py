"""真实 DashScope rerank 冒烟。

用法（backend 目录）：
    uv run python scripts/smoke_rerank.py \
      --query "预付款比例过高，合同生效后支付50%" \
      --doc "合同生效后5个工作日内支付合同总价50%的预付款" \
      --doc "争议由乙方所在地人民法院管辖"

可选：
    --style openai --model qwen3-rerank \
    --base-url https://dashscope.aliyuncs.com/compatible-api/v1/reranks
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.rag.rerank import DashScopeReranker  # noqa: E402


async def main_async(args: argparse.Namespace) -> int:
    config = replace(
        settings,
        rerank_enabled=True,
        rerank_api_style=args.style or settings.rerank_api_style,
        rerank_model=args.model or settings.rerank_model,
        rerank_base_url=args.base_url or settings.rerank_base_url,
        rerank_instruct=args.instruct or settings.rerank_instruct,
    )
    reranker = DashScopeReranker(config)
    print(
        "enabled:", reranker.enabled,
        "style:", config.rerank_api_style,
        "model:", config.rerank_model,
    )
    print("url:", config.rerank_base_url)
    print("api_key_present:", bool(config.rerank_api_key))
    if not reranker.enabled:
        print("rerank 未配置（RERANK_API_KEY / RERANK_BASE_URL 缺失）")
        return 2

    ranked = await reranker.rerank(args.query, args.doc, top_n=len(args.doc))
    print("ranked:", ranked)
    print("last_error:", reranker.last_error)
    if not ranked:
        return 1
    indices = [index for index, _ in ranked]
    if len(set(indices)) != len(indices):
        print("FAIL: 返回了重复 index")
        return 1
    if any(index < 0 or index >= len(args.doc) for index in indices):
        print("FAIL: index 越界")
        return 1
    scores = [score for _, score in ranked]
    if scores != sorted(scores, reverse=True):
        print("FAIL: 分数未降序")
        return 1
    # 第一条文档是默认相关文档；要求它进入 top-2。
    if 0 not in indices[: min(2, len(indices))]:
        print("FAIL: 相关文档未进入 top-2")
        return 1
    print("OK: rerank API 可用，相关文档进入 top-2")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--doc", action="append", required=True)
    parser.add_argument("--style", choices=["native", "openai"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--instruct", default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
