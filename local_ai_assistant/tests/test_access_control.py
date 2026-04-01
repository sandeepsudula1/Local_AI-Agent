"""
tests/test_access_control.py
==============================
Unit tests for ``core.access_control.check_access_query``.

All tests run without any real filesystem, LLM, or vector DB.
"""

from __future__ import annotations

import pytest

from core.access_control import AccessDecision, check_access_query, ALLOWED_FOLDERS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ac(query: str, last_folder: str | None = None) -> AccessDecision:
    return check_access_query(query, last_folder=last_folder)


# ---------------------------------------------------------------------------
# Case 3 — global / system requests
# ---------------------------------------------------------------------------

class TestCase3Global:
    def test_all_system_files_blocked(self):
        d = _ac("summarize all system files")
        assert d.action == "BLOCK"

    def test_entire_computer_blocked(self):
        d = _ac("can you read the entire computer")
        assert d.action == "BLOCK"

    def test_every_file_blocked(self):
        d = _ac("show every file")
        assert d.action == "BLOCK"


# ---------------------------------------------------------------------------
# Case 2 — concrete path in query
# ---------------------------------------------------------------------------

class TestCase2Path:
    def test_allowed_folder_content_op_returns_allow_folder(self):
        d = _ac(f"summarize documents in {ALLOWED_FOLDERS[0]}")
        assert d.action == "ALLOW_FOLDER"
        assert d.folder_path == ALLOWED_FOLDERS[0]

    def test_denied_path_blocked(self):
        d = _ac(r"explain files in C:\Windows\System32")
        assert d.action == "BLOCK"

    def test_allowed_access_question_blocked_affirmative(self):
        d = _ac(f"can you access {ALLOWED_FOLDERS[0]}")
        assert d.action == "BLOCK"
        assert ALLOWED_FOLDERS[0] in d.message


# ---------------------------------------------------------------------------
# Case 5 — folder follow-up with last_folder memory
# ---------------------------------------------------------------------------

class TestCase5FollowUp:
    def test_that_folder_with_memory_returns_allow_folder(self):
        d = _ac("summarize that folder", last_folder=ALLOWED_FOLDERS[0])
        assert d.action == "ALLOW_FOLDER"
        assert d.folder_path == ALLOWED_FOLDERS[0]

    def test_that_folder_without_memory_returns_clarify(self):
        d = _ac("show documents in that folder")
        assert d.action == "CLARIFY"


# ---------------------------------------------------------------------------
# Case 6 — vague folder
# ---------------------------------------------------------------------------

class TestCase6Vague:
    def test_vague_folder_no_memory_returns_clarify(self):
        d = _ac("summarize this folder")
        assert d.action == "CLARIFY"


# ---------------------------------------------------------------------------
# Case 1 — generic access question
# ---------------------------------------------------------------------------

class TestCase1AccessQuestion:
    def test_do_you_have_access_blocked(self):
        d = _ac("do you have access to my files")
        assert d.action == "BLOCK"

    def test_where_do_docs_come_from_blocked(self):
        d = _ac("where do you get documents from")
        assert d.action == "BLOCK"


# ---------------------------------------------------------------------------
# Case 7 — short ambiguous folder reference
# ---------------------------------------------------------------------------

class TestCase7Ambiguous:
    def test_which_folder_returns_clarify(self):
        d = _ac("which folder?")
        assert d.action == "CLARIFY"

    def test_document_folder_returns_clarify(self):
        d = _ac("document folder")
        assert d.action == "CLARIFY"


# ---------------------------------------------------------------------------
# Case 8 — bare filename, no folder context (NEW Phase 4)
# ---------------------------------------------------------------------------

class TestCase8FilenameNoFolder:
    """Bare filenames should trigger a folder-clarification question."""

    @pytest.mark.parametrize("query", [
        "Explain sp.txt",
        "summarize report.pdf",
        "show me data.csv",
        "what is in notes.docx",
        "analyze script.py",
    ])
    def test_filename_no_folder_returns_clarify(self, query: str):
        d = _ac(query)
        assert d.action == "CLARIFY"
        assert "?" in d.message or "folder" in d.message.lower()

    @pytest.mark.parametrize("query", [
        "Explain sp.txt",
        "summarize report.pdf",
        "show me data.csv",
    ])
    def test_clarify_message_contains_filename(self, query: str):
        d = _ac(query)
        assert d.action == "CLARIFY"
        # The clarifying question should name the file
        filename = query.rsplit(None, 1)[-1]
        assert filename in d.message

    def test_filename_with_last_folder_returns_allow_folder(self):
        """When last_folder is known, resolve the scope immediately."""
        folder = ALLOWED_FOLDERS[0]
        d = _ac("Explain sp.txt", last_folder=folder)
        assert d.action == "ALLOW_FOLDER"
        assert d.folder_path == folder

    def test_filename_with_full_path_uses_case2_not_case8(self):
        """Full paths (e.g. C:\\folder\\sp.txt) are handled by Case 2, not Case 8."""
        path = ALLOWED_FOLDERS[0] + r"\sp.txt"
        d = _ac(f"explain {path}")
        # Case 2a: allowed path + content op → ALLOW_FOLDER (not CLARIFY)
        assert d.action == "ALLOW_FOLDER"
        assert d.folder_path != ""

    def test_normal_query_without_filename_passes(self):
        """Queries with no filename pattern must not be intercepted by Case 8."""
        d = _ac("how many employees are there?")
        assert d.action == "PASS"

    def test_general_chat_passes(self):
        d = _ac("what is the capital of France?")
        assert d.action == "PASS"

    def test_filename_with_denied_path_still_blocked(self):
        """Full path in a denied location must still BLOCK via Case 2."""
        d = _ac(r"explain C:\Windows\System32\kernel.dll")
        assert d.action == "BLOCK"
