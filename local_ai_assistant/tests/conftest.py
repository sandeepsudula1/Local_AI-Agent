"""
tests/conftest.py
==================
Shared pytest fixtures and configuration.

Fixtures
--------
- ``tmp_docs_dir``   — temporary directory with sample docs (CSV, plain text)
- ``mock_vector_db`` — stub Chroma-like object for retrieval tests
- ``mock_ollama``    — monkeypatches ``ollama.chat`` to return deterministic answers
- ``settings_override`` — overrides settings fields for a single test
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Make the project root importable when pytest is run from any directory
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_docs_dir(tmp_path: Path) -> Path:
    """Return a temporary documents directory populated with sample files."""
    (tmp_path / "company_data.csv").write_text(
        "Year,Employees,Revenue\n2023,120,5000000\n2024,150,7500000\n",
        encoding="utf-8",
    )
    (tmp_path / "report.txt").write_text(
        "Q1 results show strong growth in the robotics division.",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def mock_vector_db():
    """Return a MagicMock that mimics the ChromaDB vector store interface."""
    db = MagicMock()
    # similarity_search_with_score returns list of (Document, score) tuples
    from langchain_core.documents import Document
    fake_doc = Document(
        page_content="Year: 2024, Employees: 150, Revenue: 7500000",
        metadata={"source": "company_data.csv"},
    )
    db.similarity_search_with_score.return_value = [(fake_doc, 0.8)]
    return db


@pytest.fixture
def mock_ollama(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch ``ollama.chat`` to return a deterministic response."""
    def _fake_chat(model: str, messages: list, options: dict | None = None, **kwargs: Any) -> dict:
        return {"message": {"content": "Mock LLM response."}}

    import agents.core.general_agent as ga
    monkeypatch.setattr(ga.ollama, "chat", _fake_chat, raising=True)
    return _fake_chat


@pytest.fixture
def sample_reminders_file(tmp_path: Path) -> Path:
    """Return a temp reminders.json with two pending reminders."""
    rem_file = tmp_path / "reminders.json"
    rem_file.write_text(
        json.dumps([
            {"text": "Call Alice", "time": "2099-01-01 09:00:00", "fired": False},
            {"text": "Team meeting", "time": "2099-06-15 14:00:00", "fired": False},
        ]),
        encoding="utf-8",
    )
    return rem_file
