from types import SimpleNamespace

import httpx

from app.rag.rerank import DashScopeReranker


def _config(enabled=True, api_key="test-key", style="native"):
    return SimpleNamespace(
        rerank_enabled=enabled,
        rerank_api_key=api_key,
        rerank_api_style=style,
        rerank_instruct="",
        rerank_base_url="https://example.test/rerank",
        rerank_model="gte-rerank-v2",
        rerank_top_n=20,
        rerank_timeout_s=5,
    )


async def test_rerank_returns_sorted_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 2, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.8},
                    ]
                }
            },
        )

    reranker = DashScopeReranker(
        _config(), transport=httpx.MockTransport(handler)
    )
    ranked = await reranker.rerank("预付款", ["doc-a", "doc-b", "doc-c"])
    assert ranked == [(2, 0.9), (0, 0.8)]
    assert reranker.last_error is None


async def test_rerank_failure_returns_none_and_records_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    reranker = DashScopeReranker(
        _config(), transport=httpx.MockTransport(handler)
    )
    ranked = await reranker.rerank("预付款", ["doc-a", "doc-b"])
    assert ranked is None
    assert "boom" in (reranker.last_error or "")


async def test_rerank_disabled_does_not_call_transport():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    reranker = DashScopeReranker(
        _config(enabled=False), transport=httpx.MockTransport(handler)
    )
    assert reranker.enabled is False
    assert await reranker.rerank("q", ["doc"]) is None
    assert called is False


async def test_rerank_empty_results_falls_back():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"results": []}})

    reranker = DashScopeReranker(
        _config(), transport=httpx.MockTransport(handler)
    )
    assert await reranker.rerank("q", ["doc"]) is None
    assert reranker.last_error == "rerank 返回空结果"


async def test_retriever_applies_reranker_order():
    from dataclasses import replace

    from app.config import settings
    from app.rag.corpus import load_kb
    from app.rag.retriever import HybridRetriever

    class FakeReranker:
        enabled = True
        last_error = None

        async def rerank(self, query, documents, top_n=None):
            return [(len(documents) - 1, 1.0)]

    config = replace(settings, rerank_enabled=True, rerank_api_key="test-key")
    retriever = HybridRetriever(load_kb(), config)
    retriever.reranker = FakeReranker()

    results = await retriever.retrieve(
        "预付款比例过高 付款方式 验收后支付 违约金",
        top_k=3,
        doc_types=["law"],
    )
    assert results
    assert results[0].rerank_score == 1.0
    assert retriever.rerank_mode == "dashscope"


async def test_openai_style_flat_payload_and_results():
    import json

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.4},
                ]
            },
        )

    config = _config(style="openai")
    config.rerank_model = "qwen3-rerank"
    config.rerank_base_url = "https://example.test/reranks"
    reranker = DashScopeReranker(config, transport=httpx.MockTransport(handler))
    ranked = await reranker.rerank("q", ["a", "b"], top_n=2)
    assert ranked == [(1, 0.9), (0, 0.4)]
    assert captured["payload"]["query"] == "q"
    assert captured["payload"]["documents"] == ["a", "b"]
    assert "input" not in captured["payload"]
