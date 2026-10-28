"""混合检索：BM25 + 向量 + RRF。

元数据过滤策略：
- 法条：默认全量参与排序（语料只有 32 条，硬过滤容易漏掉关键法条）；
- Playbook：按 Agent 负责的类别过滤，避免三个 Agent 都拿到全部规则。
如果向量服务不可用，自动降级为 BM25，并在 warnings 中说明。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, settings
from app.rag.bm25 import BM25Index
from app.rag.corpus import KBDocument, KnowledgeBase, load_kb
from app.rag.embeddings import EmbeddingStore
from app.rag.rerank import DashScopeReranker

RRF_K = 60


@dataclass
class RetrievedDoc:
    document: KBDocument
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None
    rerank_score: float | None = None


class HybridRetriever:
    def __init__(self, kb: KnowledgeBase, config: Settings = settings) -> None:
        self.kb = kb
        self.config = config
        self.bm25 = BM25Index(kb.documents)
        self.embeddings = EmbeddingStore(kb.documents, config)
        self.reranker = DashScopeReranker(config)
        self.last_warning: str | None = None

    @property
    def retrieval_mode(self) -> str:
        return self.embeddings.embedding_mode

    @property
    def rerank_mode(self) -> str:
        if not self.config.rerank_enabled:
            return "off"
        if not self.reranker.enabled:
            return "unavailable"
        if self.reranker.last_error:
            return "error"
        return "dashscope"

    async def warmup(self) -> None:
        """live 模式下提前建好向量缓存；失败只记录 warning。"""
        if not self.embeddings.available:
            return
        ok = await self.embeddings.ensure_built()
        if not ok:
            self.last_warning = "向量检索不可用，已降级为 BM25（不影响规则与报告）"

    def _allowed_ids(
        self,
        doc_types: list[str] | None,
        categories: list[str] | None,
    ) -> set[str] | None:
        if not doc_types and not categories:
            return None
        allowed: set[str] = set()
        for document in self.kb.documents:
            if doc_types and document.doc_type not in doc_types:
                continue
            if document.doc_type == "rule" and categories:
                if document.metadata.get("category") not in categories:
                    continue
            allowed.add(document.id)
        return allowed

    async def retrieve(
        self,
        query: str,
        top_k: int = 8,
        doc_types: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> list[RetrievedDoc]:
        allowed = self._allowed_ids(doc_types, categories)
        if allowed is not None and not allowed:
            allowed = None
        bm25_results = self.bm25.search(query, top_k=top_k * 2, allowed_ids=allowed)

        dense_results: list[tuple[str, float]] = []
        if self.embeddings.available:
            if await self.embeddings.ensure_built():
                dense_results = await self.embeddings.search(
                    query, top_k=top_k * 2, allowed_ids=allowed
                )
            else:
                self.last_warning = "向量检索不可用，已降级为 BM25（不影响规则与报告）"

        scores: dict[str, float] = {}
        bm25_rank: dict[str, int] = {}
        dense_rank: dict[str, int] = {}
        for rank, (document, _score) in enumerate(bm25_results):
            scores[document.id] = scores.get(document.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            bm25_rank[document.id] = rank + 1
        for rank, (doc_id, _score) in enumerate(dense_results):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            dense_rank[doc_id] = rank + 1

        by_id = {document.id: document for document in self.kb.documents}
        results = [
            RetrievedDoc(
                document=by_id[doc_id],
                score=score,
                bm25_rank=bm25_rank.get(doc_id),
                dense_rank=dense_rank.get(doc_id),
            )
            for doc_id, score in scores.items()
            if doc_id in by_id
        ]
        results.sort(key=lambda item: item.score, reverse=True)

        if self.reranker.enabled and results:
            candidates = results[: max(top_k * 3, 20)]
            ranked = await self.reranker.rerank(
                query,
                [item.document.text for item in candidates],
                top_n=len(candidates),
            )
            if ranked:
                used: set[int] = set()
                reranked: list[RetrievedDoc] = []
                for index, score in ranked:
                    if index in used or not 0 <= index < len(candidates):
                        continue
                    used.add(index)
                    item = candidates[index]
                    item.rerank_score = score
                    reranked.append(item)
                reranked.extend(
                    item for index, item in enumerate(candidates) if index not in used
                )
                results = reranked + results[len(candidates) :]
            elif self.reranker.last_error:
                self.last_warning = (
                    f"rerank 不可用，已回退 RRF：{self.reranker.last_error}"
                )

        return results[:top_k]


_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever(load_kb())
    return _retriever


def reset_retriever() -> None:
    """测试/重建索引时清空单例。"""
    global _retriever
    _retriever = None
