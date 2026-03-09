"""Tests for engines/reranker.py — no LLM / heavy deps required."""
import pytest

from engines.reranker import KeywordReranker, get_reranker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeDoc:
    """Minimal Document stub — only page_content needed by reranker."""
    def __init__(self, content: str, source: str = "test.txt"):
        self.page_content = content
        self.metadata = {"source": source}


def _make_docs(*texts) -> list[tuple[_FakeDoc, float]]:
    """Return a list of (doc, score) pairs with decreasing vector scores."""
    return [(_FakeDoc(t), float(i + 1)) for i, t in enumerate(texts)]


# ---------------------------------------------------------------------------
# KeywordReranker
# ---------------------------------------------------------------------------

class TestKeywordReranker:
    @pytest.fixture()
    def reranker(self):
        return KeywordReranker()

    def test_rerank_returns_list(self, reranker):
        docs = _make_docs("Python is great", "Java is verbose", "Rust is fast")
        result = reranker.rerank("Python performance", docs, top_k=2)
        assert isinstance(result, list)

    def test_top_k_respected(self, reranker):
        docs = _make_docs("a", "b", "c", "d", "e")
        result = reranker.rerank("query", docs, top_k=3)
        assert len(result) <= 3

    def test_relevant_doc_ranked_higher(self, reranker):
        docs = _make_docs(
            "The company has 500 employees in 2024",  # highly relevant
            "The weather is nice today",              # not relevant
            "Python programming guide",               # somewhat relevant
        )
        result = reranker.rerank("how many employees", docs, top_k=3)
        contents = [doc.page_content for doc, _ in result]
        assert "500 employees" in contents[0]

    def test_empty_input_returns_empty(self, reranker):
        result = reranker.rerank("query", [], top_k=3)
        assert result == []

    def test_fewer_docs_than_top_k(self, reranker):
        docs = _make_docs("only one doc")
        result = reranker.rerank("query", docs, top_k=5)
        assert len(result) == 1

    def test_phrase_bonus(self, reranker):
        """A doc containing the exact query phrase should score higher."""
        docs = _make_docs(
            "machine learning techniques",   # exact phrase
            "deep neural networks",          # related but no exact phrase
        )
        result = reranker.rerank("machine learning", docs, top_k=2)
        assert result[0][0].page_content == "machine learning techniques"

    def test_result_format(self, reranker):
        """Each result should be a (doc, float) tuple."""
        docs = _make_docs("hello world")
        result = reranker.rerank("hello", docs, top_k=1)
        assert len(result) == 1
        doc, score = result[0]
        assert hasattr(doc, "page_content")
        assert isinstance(score, float)

    def test_query_with_stopwords_only(self, reranker):
        """All-stopword query should not crash even if scoring is 0."""
        docs = _make_docs("some document content", "another document")
        result = reranker.rerank("the and or but", docs, top_k=2)
        assert isinstance(result, list)

    def test_duplicate_docs_handled(self, reranker):
        """Duplicate doc content should not cause errors."""
        docs = _make_docs("same content", "same content", "same content")
        result = reranker.rerank("same", docs, top_k=2)
        assert len(result) <= 2


# ---------------------------------------------------------------------------
# get_reranker factory
# ---------------------------------------------------------------------------

class TestGetReranker:
    def test_default_returns_keyword_reranker(self):
        r = get_reranker(prefer_cross_encoder=False)
        assert isinstance(r, KeywordReranker)

    def test_returns_reranker_with_rerank_method(self):
        r = get_reranker()
        assert callable(getattr(r, "rerank", None))

    def test_cross_encoder_falls_back_gracefully(self):
        """Even if cross-encoder is requested, should not crash."""
        r = get_reranker(prefer_cross_encoder=True)
        assert callable(getattr(r, "rerank", None))


# ---------------------------------------------------------------------------
# Integration: retrieve_top_k and retrieve_and_rerank
# ---------------------------------------------------------------------------

class TestRagEngineHelpers:
    """Smoke tests for the new RAG helpers (no real vector DB needed)."""

    def test_retrieve_top_k_returns_empty_for_none_db(self):
        from engines.rag_engine import retrieve_top_k
        result = retrieve_top_k("test", None, k=5)
        assert result == []

    def test_retrieve_and_rerank_returns_empty_for_none_db(self):
        from engines.rag_engine import retrieve_and_rerank
        result = retrieve_and_rerank("test", None, initial_k=10, top_k=3)
        assert result == []
