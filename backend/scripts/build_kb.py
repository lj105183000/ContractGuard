"""预构建知识库索引。

用法（backend 目录）：
    uv run python scripts/build_kb.py                  # 校验 + 构建 BM25
    uv run python scripts/build_kb.py --embeddings     # live 模式下预建向量缓存
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.rag.corpus import load_kb  # noqa: E402
from app.rag.retriever import get_retriever, reset_retriever  # noqa: E402


async def run(embeddings: bool) -> None:
    kb = load_kb()
    print(
        f"已加载 {len(kb.legal_articles)} 条法条、{len(kb.rules)} 条规则、"
        f"{len(kb.samples)} 份示例合同"
    )
    reset_retriever()
    retriever = get_retriever()
    print(f"BM25 索引就绪：{len(kb.documents)} 条文档；模式 {retriever.retrieval_mode}")
    if not embeddings:
        print("未请求向量索引。需要时加 --embeddings（要求 LLM_MODE=live + API Key）。")
        return
    if not settings.embedding_live:
        print("当前不是 live 模式或没有 Embedding API Key，跳过向量索引。")
        return
    print(f"正在调用 {settings.embedding_model} 构建/加载向量缓存…")
    await retriever.warmup()
    if retriever.last_warning:
        print(f"警告：{retriever.last_warning}")
    else:
        print(f"向量索引就绪：{retriever.retrieval_mode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embeddings", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.embeddings))


if __name__ == "__main__":
    main()
