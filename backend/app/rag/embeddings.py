"""百炼 text-embedding-v3 向量检索与本地缓存。

- 语料只有几十条，用 numpy 精确余弦即可，不引入向量数据库；
- 缓存指纹包含模型名、维度和全部文档文本，语料变动会自动重建；
- 没有 Key / 调用失败时返回不可用，由 Retriever 降级到 BM25。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

import numpy as np
from openai import AsyncOpenAI

from app.config import INDEX_CACHE_DIR, Settings, settings
from app.rag.corpus import KBDocument

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MAX_RETRIES = 3


class EmbeddingError(RuntimeError):
    """向量服务不可用。"""


class EmbeddingStore:
    def __init__(
        self,
        documents: list[KBDocument],
        config: Settings = settings,
        cache_dir: Path = INDEX_CACHE_DIR,
    ) -> None:
        self.documents = documents
        self.config = config
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = cache_dir / "embeddings.npz"
        self.meta_path = cache_dir / "embeddings_meta.json"
        self.client = (
            AsyncOpenAI(
                api_key=config.embedding_api_key,
                base_url=config.embedding_base_url,
                timeout=90,
                max_retries=0,
            )
            if config.embedding_live
            else None
        )
        self.doc_ids = [document.id for document in documents]
        self.fingerprint = self._fingerprint()
        self.vectors: np.ndarray | None = None
        self.actual_dim: int | None = None
        self._supports_dimensions = True
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.client is not None

    @property
    def ready(self) -> bool:
        return self.vectors is not None

    @property
    def embedding_mode(self) -> str:
        return "hybrid" if self.ready else "bm25_only"

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.config.embedding_model.encode("utf-8"))
        digest.update(str(self.config.embedding_dim).encode("ascii"))
        digest.update(self.config.embedding_base_url.encode("utf-8"))
        for document in self.documents:
            digest.update(document.id.encode("utf-8"))
            digest.update(document.title.encode("utf-8"))
            digest.update(document.text.encode("utf-8"))
        return digest.hexdigest()

    def _load_cache(self) -> bool:
        if not self.cache_path.exists() or not self.meta_path.exists():
            return False
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != self.fingerprint:
                return False
            with np.load(self.cache_path, allow_pickle=False) as data:
                ids = [str(item) for item in data["ids"].tolist()]
                vectors = np.asarray(data["vectors"], dtype=np.float32)
            if ids != self.doc_ids:
                return False
            self.vectors = vectors
            self.actual_dim = int(vectors.shape[1])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取向量缓存失败，将重建：%s", exc)
            return False

    def _save_cache(self) -> None:
        if self.vectors is None:
            return
        np.savez_compressed(
            self.cache_path,
            ids=np.asarray(self.doc_ids, dtype=str),
            vectors=self.vectors.astype(np.float32),
        )
        self.meta_path.write_text(
            json.dumps(
                {
                    "fingerprint": self.fingerprint,
                    "model": self.config.embedding_model,
                    "dim": self.actual_dim,
                    "count": len(self.doc_ids),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    async def ensure_built(self) -> bool:
        """构建/加载向量索引；不可用时返回 False，不抛异常。"""
        if not self.available:
            return False
        if self.vectors is not None:
            return True
        async with self._lock:
            if self.vectors is not None:
                return True
            if self._load_cache():
                return True
            try:
                texts = [f"{document.title}\n{document.text}" for document in self.documents]
                vectors = await self._embed_texts(texts)
            except Exception as exc:  # noqa: BLE001
                logger.warning("构建向量索引失败，将降级 BM25：%s", exc)
                return False
            self.vectors = vectors
            self.actual_dim = int(vectors.shape[1])
            self._save_cache()
            return True

    async def _embed_texts(self, texts: list[str]) -> np.ndarray:
        if self.client is None:
            raise EmbeddingError("未配置向量模型")
        all_vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            vectors = await self._embed_batch(batch)
            all_vectors.extend(vectors)
        array = np.asarray(all_vectors, dtype=np.float32)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return array / norms

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                kwargs = {
                    "model": self.config.embedding_model,
                    "input": batch,
                    "encoding_format": "float",
                }
                if self._supports_dimensions:
                    kwargs["dimensions"] = self.config.embedding_dim
                response = await self.client.embeddings.create(**kwargs)  # type: ignore[union-attr]
                return [list(item.embedding) for item in response.data]
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                # 部分兼容接口不支持 dimensions 参数，退一次不带维度的调用
                if self._supports_dimensions:
                    self._supports_dimensions = False
                    try:
                        response = await self.client.embeddings.create(  # type: ignore[union-attr]
                            model=self.config.embedding_model,
                            input=batch,
                            encoding_format="float",
                        )
                        return [list(item.embedding) for item in response.data]
                    except Exception as inner:  # noqa: BLE001
                        last_error = inner
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)
        raise EmbeddingError(f"embedding 调用失败：{last_error}")

    async def embed_query(self, query: str) -> np.ndarray | None:
        if not await self.ensure_built():
            return None
        try:
            vector = await self._embed_texts([query])
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询向量失败：%s", exc)
            return None
        return vector[0]

    async def search(
        self,
        query: str,
        top_k: int = 8,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        if self.vectors is None:
            return []
        query_vector = await self.embed_query(query)
        if query_vector is None:
            return []
        scores = self.vectors @ query_vector
        order = np.argsort(scores)[::-1]
        results: list[tuple[str, float]] = []
        for index in order:
            doc_id = self.doc_ids[int(index)]
            if allowed_ids is not None and doc_id not in allowed_ids:
                continue
            results.append((doc_id, float(scores[int(index)])))
            if len(results) >= top_k:
                break
        return results
