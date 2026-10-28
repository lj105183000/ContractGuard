"""BM25 检索（jieba 中文分词）。"""

from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path

with warnings.catch_warnings():
    # jieba 0.42 在 Python 3.12 下会触发无害的 SyntaxWarning
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

from rank_bm25 import BM25Okapi

from app.config import LEGAL_TERMS_PATH
from app.rag.corpus import KBDocument

jieba.setLogLevel(logging.WARNING)
_terms_loaded = False

STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "及",
    "或",
    "是",
    "在",
    "对",
    "为",
    "以",
    "等",
    "应",
    "应当",
    "可以",
    "一方",
    "当事人",
    "合同",
}


def _ensure_terms() -> None:
    global _terms_loaded
    if _terms_loaded:
        return
    if LEGAL_TERMS_PATH.exists():
        jieba.load_userdict(str(LEGAL_TERMS_PATH))
    _terms_loaded = True


def tokenize(text: str) -> list[str]:
    _ensure_terms()
    tokens = []
    for token in jieba.lcut(text.lower()):
        token = token.strip()
        if not token or token in STOPWORDS:
            continue
        if not re.search(r"[\w一-鿿]", token):
            continue
        tokens.append(token)
    return tokens


class BM25Index:
    def __init__(self, documents: list[KBDocument]) -> None:
        self.documents = documents
        self.by_id = {document.id: document for document in documents}
        self.corpus_tokens = [tokenize(document.title + "\n" + document.text) for document in documents]
        self.bm25 = BM25Okapi(self.corpus_tokens) if self.corpus_tokens else None

    def search(
        self,
        query: str,
        top_k: int = 8,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[KBDocument, float]]:
        if self.bm25 is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = self.bm25.get_scores(query_tokens)
        ranked = sorted(
            zip(self.documents, scores),
            key=lambda item: item[1],
            reverse=True,
        )
        results: list[tuple[KBDocument, float]] = []
        for document, score in ranked:
            if allowed_ids is not None and document.id not in allowed_ids:
                continue
            if score <= 0:
                continue
            results.append((document, float(score)))
            if len(results) >= top_k:
                break
        return results
