"""DashScope text-rerank 客户端。

可选能力：只在 ``RERANK_ENABLED=1`` 且配置了 Key 时启用。
接口失败、超时或返回空结果时返回 ``None``，由调用方回退到 RRF。
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, settings

logger = logging.getLogger(__name__)


class DashScopeReranker:
    def __init__(
        self,
        config: Settings = settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.rerank_enabled
            and self.config.rerank_api_key
            and self.config.rerank_base_url
        )

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]] | None:
        """返回 [(原始下标, 相关性分数)]，按分数降序；失败返回 None。"""
        if not self.enabled or not documents:
            return None
        target_n = top_n or min(len(documents), self.config.rerank_top_n)
        if self.config.rerank_api_style == "openai":
            # qwen3-rerank 的 OpenAI 兼容扁平接口。
            payload = {
                "model": self.config.rerank_model,
                "query": query,
                "documents": documents,
                "top_n": target_n,
            }
            if self.config.rerank_instruct:
                payload["instruct"] = self.config.rerank_instruct
        else:
            payload = {
                "model": self.config.rerank_model,
                "input": {"query": query, "documents": documents},
                "parameters": {
                    "return_documents": False,
                    "top_n": target_n,
                },
            }
        headers = {
            "Authorization": f"Bearer {self.config.rerank_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.config.rerank_timeout_s,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.config.rerank_base_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.warning("rerank 调用失败，回退 RRF：%s", exc)
            return None

        results = (
            data.get("results")
            or (data.get("output") or {}).get("results")
            or []
        )
        ranked: list[tuple[int, float]] = []
        for item in results:
            try:
                index = int(item["index"])
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(documents):
                ranked.append((index, score))
        if not ranked:
            self.last_error = "rerank 返回空结果"
            return None
        self.last_error = None
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked
