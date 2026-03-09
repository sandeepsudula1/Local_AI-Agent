"""
engines/reranker.py
====================
Reranking layer for the RAG pipeline.

After the vector store returns the initial top-K results (by embedding
similarity), a reranker rescores them by a different signal and selects
the final top-N that are passed to the LLM.

Two implementations are provided
---------------------------------
1. ``KeywordReranker`` (default, zero extra dependencies)
   Scores each chunk by keyword overlap + exact phrase matches between
   the query and the chunk text.  Fast and good enough for most queries.

2. ``CrossEncoderReranker`` (optional, requires ``sentence-transformers``)
   Uses a bi-directional cross-encoder to compute real relevance scores.
   Falls back to ``KeywordReranker`` automatically if the model cannot be
   loaded.

Usage::

    from engines.reranker import get_reranker

    reranker = get_reranker()               # auto-selects best available
    top_docs = reranker.rerank(
        query="how many employees in 2024",
        docs=initial_docs,                  # list of (Document, score)
        top_k=3
    )
"""

from __future__ import annotations

import re
from typing import Any

from core.logging_config import get_logger

log = get_logger(__name__)

# (Document, float_score) pairs — same type as Chroma's output
_DocScore = tuple[Any, float]


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseReranker:
    """Common interface for all rerankers."""

    def rerank(
        self,
        query: str,
        docs: list[_DocScore],
        top_k: int = 3,
    ) -> list[_DocScore]:
        """Return *top_k* (doc, score) pairs ordered best-first.

        Subclasses override ``_score_one`` to implement their scoring logic.
        """
        if not docs:
            return []

        scored: list[tuple[float, _DocScore]] = []
        for doc_score in docs:
            doc, orig_score = doc_score
            relevance = self._score_one(query, doc.page_content)
            scored.append((relevance, (doc, orig_score)))

        # Sort by relevance descending (higher = more relevant)
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ds for _, ds in scored[:top_k]]

    def _score_one(self, query: str, text: str) -> float:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# KeywordReranker
# ---------------------------------------------------------------------------

class KeywordReranker(BaseReranker):
    """Fast keyword-overlap reranker — no extra dependencies."""

    # Stop words to ignore when computing overlap
    _STOP: frozenset[str] = frozenset({
        "what", "when", "where", "which", "that", "this", "have", "from",
        "with", "does", "about", "were", "will", "into", "been", "they",
        "them", "said", "tell", "show", "give", "know", "more", "some",
        "like", "also", "used", "then", "than", "there", "their", "these",
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "her", "was", "one", "our", "out", "day", "get",
    })

    def _query_keywords(self, query: str) -> list[str]:
        words = re.findall(r"[a-z0-9]+", query.lower())
        return [w for w in words if len(w) > 2 and w not in self._STOP]

    def _score_one(self, query: str, text: str) -> float:
        keywords = self._query_keywords(query)
        if not keywords:
            return 0.0

        text_lower = text.lower()
        # Exact phrase bonus (query as a single substring)
        phrase_bonus = 2.0 if query.lower() in text_lower else 0.0

        # Keyword count (normalised by total query keywords)
        kw_score = sum(
            min(text_lower.count(kw), 3)   # cap per-keyword contribution
            for kw in keywords
        ) / len(keywords)

        # Length penalty — prefer shorter, denser matches
        length_penalty = max(0.0, 1.0 - len(text) / 8000)

        return kw_score + phrase_bonus + length_penalty * 0.1


# ---------------------------------------------------------------------------
# CrossEncoderReranker (optional)
# ---------------------------------------------------------------------------

class CrossEncoderReranker(BaseReranker):
    """Semantic cross-encoder reranker (requires sentence-transformers ≥ 2.2)."""

    _MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(self) -> None:
        self._model = None
        self._loaded = False

    def _load(self) -> bool:
        if self._loaded:
            return self._model is not None
        self._loaded = True
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]
            self._model = CrossEncoder(self._MODEL_NAME)
            log.info("CrossEncoderReranker loaded: %s", self._MODEL_NAME)
            return True
        except Exception as exc:
            log.warning("CrossEncoder unavailable (%s); falling back to keyword reranker", exc)
            return False

    def rerank(
        self,
        query: str,
        docs: list[_DocScore],
        top_k: int = 3,
    ) -> list[_DocScore]:
        if not self._load() or self._model is None:
            return KeywordReranker().rerank(query, docs, top_k)

        pairs = [(query, doc.page_content) for doc, _ in docs]
        try:
            scores: list[float] = self._model.predict(pairs).tolist()
        except Exception as exc:
            log.warning("CrossEncoder.predict failed: %s", exc)
            return KeywordReranker().rerank(query, docs, top_k)

        scored = sorted(
            zip(scores, docs),
            key=lambda x: x[0],
            reverse=True,
        )
        return [ds for _, ds in scored[:top_k]]

    def _score_one(self, query: str, text: str) -> float:  # pragma: no cover
        raise NotImplementedError("CrossEncoderReranker uses batch predict; _score_one not used")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_reranker(prefer_cross_encoder: bool = False) -> BaseReranker:
    """Return the best available reranker.

    Parameters
    ----------
    prefer_cross_encoder:
        If True, try to load the cross-encoder first.  Falls back to
        ``KeywordReranker`` if sentence-transformers is not installed or
        the model fails to load.
    """
    if prefer_cross_encoder:
        r = CrossEncoderReranker()
        r._load()          # probe — sets r._model or None
        if r._model is not None:
            return r
    return KeywordReranker()
