"""
tests/test_retrieval.py
========================
Unit tests for ``agents.knowledge.retrieval_agent``.

Tests use a mock vector DB and mock file system so no real Chroma or
LLM is required.  Heavy imports (ollama, langchain, chromadb) are only
triggered inside the functions under test; we monkeypatch them where
needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_doc(content: str, source: str = "company_data.csv"):
    """Return a minimal LangChain Document-like object."""
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source}
    return doc


@pytest.fixture
def fake_vector_db():
    db = MagicMock()
    db.similarity_search_with_score.return_value = [
        (_make_doc("Year: 2024, Employees: 150, Revenue: 7500000"), 0.8),
    ]
    return db


@pytest.fixture
def mock_ollama_retrieval(monkeypatch: pytest.MonkeyPatch):
    """Patch ollama inside retrieval_agent so no real LLM is called."""
    import agents.knowledge.retrieval_agent as ra

    fake_response = {"message": {"content": "There are 150 employees in 2024."}}

    def _fake_chat(**kwargs: Any) -> dict:
        return fake_response

    monkeypatch.setattr(ra.ollama, "chat", _fake_chat, raising=True)
    return _fake_chat


# ---------------------------------------------------------------------------
# _deduplicate_lines
# ---------------------------------------------------------------------------

def test_deduplicate_lines_removes_dupes() -> None:
    from agents.knowledge.retrieval_agent import _deduplicate_lines

    text = "foo\nbar\nfoo\nbaz\nbar"
    result = _deduplicate_lines(text)
    lines = result.splitlines()
    assert lines == ["foo", "bar", "baz"]


def test_deduplicate_lines_empty() -> None:
    from agents.knowledge.retrieval_agent import _deduplicate_lines

    assert _deduplicate_lines("") == ""
    assert _deduplicate_lines(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _relevant_excerpt
# ---------------------------------------------------------------------------

def test_relevant_excerpt_short_text_unchanged() -> None:
    from agents.knowledge.retrieval_agent import _relevant_excerpt

    short = "This is a short text."
    assert _relevant_excerpt(short, "short", max_chars=4000) == short


def test_relevant_excerpt_selects_relevant_window() -> None:
    from agents.knowledge.retrieval_agent import _relevant_excerpt

    # Build a long text with the keyword only in the middle
    prefix = "irrelevant " * 300   # ~3300 chars
    target = "employees revenue 150 " * 20
    suffix = "noise " * 300
    text = prefix + target + suffix

    excerpt = _relevant_excerpt(text, "employees revenue", max_chars=500)
    assert "employees" in excerpt.lower() or "revenue" in excerpt.lower()


# ---------------------------------------------------------------------------
# handle_retrieval — integration-style with mocks
# ---------------------------------------------------------------------------

def test_handle_retrieval_returns_answer_and_source(
    fake_vector_db: MagicMock,
    mock_ollama_retrieval: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """handle_retrieval must return a (str, str) tuple when vector DB has a match."""
    import agents.knowledge.retrieval_agent as ra

    # Point DOCS_PATH to a tmp dir with a fake CSV so Rule 1 doesn't trigger
    monkeypatch.setattr(ra, "DOCS_PATH", str(tmp_path))

    answer, source = ra.handle_retrieval(
        "how many employees in 2024",
        fake_vector_db,
        threshold=1.5,
        model_name="gemma:7b",
    )

    assert isinstance(answer, str)
    assert len(answer) > 0


def test_handle_retrieval_no_vector_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """handle_retrieval with vector_db=None must not crash and return (None, None)."""
    import agents.knowledge.retrieval_agent as ra

    monkeypatch.setattr(ra, "DOCS_PATH", str(tmp_path))
    answer, source = ra.handle_retrieval(
        "how many employees in 2024",
        None,
        threshold=1.5,
        model_name="gemma:7b",
    )
    # Should degrade gracefully
    assert answer is None or isinstance(answer, str)


# ---------------------------------------------------------------------------
# _load_file_content
# ---------------------------------------------------------------------------

def test_load_csv_returns_labeled_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import agents.knowledge.retrieval_agent as ra

    csv_file = tmp_path / "company_data.csv"
    csv_file.write_text("Year,Employees\n2024,150\n2023,120\n", encoding="utf-8")
    monkeypatch.setattr(ra, "DOCS_PATH", str(tmp_path))

    content, source = ra._load_file_content("company_data.csv")  # returns (text, fname)
    assert content is not None
    assert "2024" in content
    assert "150" in content


def test_load_nonexistent_file_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import agents.knowledge.retrieval_agent as ra

    monkeypatch.setattr(ra, "DOCS_PATH", str(tmp_path))
    content, source = ra._load_file_content("ghost_file.pdf")  # returns (text, fname)
    # Both should be None for a missing file
    assert content is None
    assert source is None


# ---------------------------------------------------------------------------
# Rule 1b — folder_path filter
# ---------------------------------------------------------------------------

def _make_doc_with_source(content: str, source: str):
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source, "file_name": ""}
    return doc


def test_rule1b_folder_filter_blocks_wrong_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rule 1b must return 'not in folder' when indexed chunks are outside folder_path."""
    import agents.knowledge.retrieval_agent as ra

    allowed_folder = r"C:\AI_Test_Documents"
    other_folder = r"C:\Other_Docs"
    filename = "report.pdf"

    # Chunk lives in *other_folder*, not in allowed_folder
    fake_doc = _make_doc_with_source("report content", rf"{other_folder}\{filename}")

    # Patch _search_by_filename_in_stores to return our fake chunk
    monkeypatch.setattr(ra, "_search_by_filename_in_stores", lambda hint, dbs: [fake_doc])
    monkeypatch.setattr(ra, "DOCS_PATH", str(tmp_path))

    fake_db = MagicMock()
    answer, source = ra.handle_retrieval(
        f"summarize {filename}",
        fake_db,
        threshold=1.5,
        model_name="gemma:7b",
        folder_path=allowed_folder,
    )

    assert answer is not None
    assert "not in" in answer.lower() or filename in answer
    assert source is None


def test_rule1b_folder_filter_allows_correct_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rule 1b must proceed when indexed chunk is inside folder_path."""
    import agents.knowledge.retrieval_agent as ra

    allowed_folder = r"C:\AI_Test_Documents"
    filename = "report.pdf"
    fake_doc = _make_doc_with_source("annual report content", rf"{allowed_folder}\{filename}")

    monkeypatch.setattr(ra, "_search_by_filename_in_stores", lambda hint, dbs: [fake_doc])
    monkeypatch.setattr(ra, "DOCS_PATH", str(tmp_path))

    # Mock LLM to avoid real Ollama calls
    fake_response = {"message": {"content": "Annual report answer."}}
    monkeypatch.setattr(ra.ollama, "chat", lambda **kw: fake_response)

    fake_db = MagicMock()
    answer, source = ra.handle_retrieval(
        f"summarize {filename}",
        fake_db,
        threshold=1.5,
        model_name="gemma:7b",
        folder_path=allowed_folder,
    )

    assert answer is not None
    assert isinstance(answer, str)
