"""
memory/conversation_memory.py
==============================
Thread-safe, optionally persistent conversation memory for the assistant.

Two kinds of memory are stored
--------------------------------
1. **Facts** — structured key/value pairs extracted from the conversation.
   e.g. ``name=Sandeep``, ``preferred_language=Python``.
   Retrieved instantly by key; injected as a system-level note into every
   LLM prompt so the assistant can answer "what is my name?".

2. **History** — ordered list of ``{"role": ..., "content": ...}`` turns,
   trimmed to the last *max_history* entries.  Used to give the LLM short-
   term conversational context ("what did we just talk about?").

Persistence
-----------
When *persist_path* is set the memory is saved as JSON after every write
so it survives a process restart.

Usage::

    from memory.conversation_memory import conversation_memory

    conversation_memory.store("name", "Sandeep")
    print(conversation_memory.retrieve("name"))   # "Sandeep"

    conversation_memory.add_turn("user", "My name is Sandeep")
    conversation_memory.add_turn("assistant", "Nice to meet you, Sandeep!")

    # Inject into an ollama messages list
    messages = conversation_memory.build_messages(
        system_prompt="You are a helpful assistant.",
        user_query="What is my name?",
    )
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger

log = get_logger(__name__)

# Patterns used to auto-extract facts from user utterances
_FACT_PATTERNS: list[tuple[str, str]] = [
    # "my name is X"
    (r"my name is ([A-Za-z][A-Za-z\s\-]{0,40})", "name"),
    # "I am called / I am named X" — must be BEFORE the generic "I am X" pattern
    (r"i(?:'m| am) (?:called|named) ([A-Za-z][A-Za-z\s\-]{1,30})", "name"),
    # "I am X" / "I'm X" (name-like word — capitalised, to avoid false matches)
    (r"i(?:'m| am) ([A-Z][a-z]{2,20})(?:\s+[A-Z][a-z]{2,20})?", "name"),
    # "call me X"
    (r"call me ([A-Za-z][A-Za-z\s\-]{1,30})", "name"),
    # "I work at X"
    (r"i work (?:at|for) ([A-Za-z][A-Za-z0-9\s\-\.,]{2,60})", "workplace"),
    # "I prefer X" / "I like X"
    (r"i (?:prefer|like|love|use) ([A-Za-z][A-Za-z0-9\s\-\+\#]{1,30})", "preference"),
    # "my email is X"
    (r"my email(?: is| address is)? ([\w\.\-]+@[\w\.\-]+\.[a-z]{2,6})", "email"),
    # "I live in / I'm from X"
    (r"i (?:live in|'?m from|am from) ([A-Za-z][A-Za-z\s\-]{2,40})", "location"),
    # "my role is / I am a X"
    (r"(?:my role is|i am a|i'?m a) ([A-Za-z][A-Za-z\s\-]{2,40})", "role"),
]


class ConversationMemory:
    """Dual-layer conversation memory: structured facts + turn history."""

    def __init__(
        self,
        max_history: int = 20,
        persist_path: Optional[Path] = None,
    ) -> None:
        self._facts: dict[str, str] = {}
        self._history: list[dict] = []
        self._max_history = max_history
        self._persist_path = persist_path
        self._lock = threading.Lock()
        # Session-only context — never persisted
        self._last_file: Optional[str] = None
        self._last_intent: Optional[str] = None
        self._last_folder: Optional[str] = None
        self._pending_query: Optional[str] = None  # query awaiting folder clarification
        self._last_email_search_results: list[dict] = []  # email search context for reply generation

        if persist_path and Path(persist_path).exists():
            self._load()

    # ── Facts API ──────────────────────────────────────────────────────────

    def store(self, key: str, value: str) -> None:
        """Store a key/value fact.  Overwrites any previous value."""
        with self._lock:
            self._facts[key.lower().strip()] = value.strip()
            log.debug("Memory: stored fact %s=%r", key, value)
            self._persist()

    def retrieve(self, key: str) -> Optional[str]:
        """Return the stored value for *key*, or ``None``."""
        with self._lock:
            return self._facts.get(key.lower().strip())

    def list_facts(self) -> dict[str, str]:
        """Return a copy of all stored facts."""
        with self._lock:
            return dict(self._facts)

    def clear_facts(self) -> None:
        """Remove all stored facts."""
        with self._lock:
            self._facts.clear()
            self._persist()

    # ── Session context (last referenced file) ─────────────────────────────

    def set_last_file(self, filename: str) -> None:
        """Record the last file the user asked about (session-only, not persisted)."""
        with self._lock:
            self._last_file = filename
            log.debug("Memory: last_file set to %r", filename)

    def get_last_file(self) -> Optional[str]:
        """Return the filename referenced in the most recent retrieval turn, or None."""
        with self._lock:
            return self._last_file

    def set_last_intent(self, intent: str) -> None:
        """Record the intent of the most recent non-trivial turn."""
        with self._lock:
            self._last_intent = intent

    def get_last_intent(self) -> Optional[str]:
        """Return the intent of the most recent non-trivial turn, or None."""
        with self._lock:
            return self._last_intent

    def set_last_folder(self, folder: str) -> None:
        """Record the last folder the user operated on (session-only, not persisted)."""
        with self._lock:
            self._last_folder = folder
            log.debug("Memory: last_folder set to %r", folder)

    def get_last_folder(self) -> Optional[str]:
        """Return the folder from the most recent folder-scoped turn, or None."""
        with self._lock:
            return self._last_folder

    # ── History API ────────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str) -> None:
        """Append one turn to the conversation history.

        *role* should be ``"user"`` or ``"assistant"``.
        The history is trimmed to the last *max_history* entries.
        """
        with self._lock:
            self._history.append({"role": role, "content": content})
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            self._persist()

    def get_history(self, last_n: int = 10) -> list[dict]:
        """Return the last *last_n* conversation turns."""
        with self._lock:
            return list(self._history[-last_n:])

    def clear_history(self) -> None:
        """Remove all conversation history."""
        with self._lock:
            self._history.clear()
            self._persist()

    # ── Pending-query (folder clarification) ──────────────────────────────

    def set_pending_query(self, query: str) -> None:
        """Save a query that is waiting for a folder-clarification reply."""
        with self._lock:
            self._pending_query = query
            log.debug("Memory: pending_query set to %.60s", query)

    def get_pending_query(self) -> Optional[str]:
        """Return the saved pending query, or None."""
        with self._lock:
            return self._pending_query

    def clear_pending_query(self) -> None:
        """Remove the saved pending query."""
        with self._lock:
            self._pending_query = None

    def clear(self) -> None:
        """Clear both facts and history."""
        with self._lock:
            self._facts.clear()
            self._history.clear()
            self._last_file = None
            self._last_intent = None
            self._last_folder = None
            self._pending_query = None
            self._persist()
        log.info("Conversation memory cleared")

    # ── Email search context ──────────────────────────────────────────────

    def set_last_email_search_results(self, emails: list[dict]) -> None:
        """Store the last email search results for context-aware reply generation.
        
        This allows "reply to first email", "reply to that email" type queries
        to reference the previous search results without re-searching.
        
        Parameters
        ----------
        emails : list[dict]
            List of email dicts from the last successful email search.
        """
        with self._lock:
            self._last_email_search_results = emails.copy() if emails else []
            log.debug("Memory: stored %d email search results", len(emails) if emails else 0)

    def get_last_email_search_results(self) -> list[dict]:
        """Return the stored email search results (empty list if none stored)."""
        with self._lock:
            return list(self._last_email_search_results) if self._last_email_search_results else []

    def clear_email_search_results(self) -> None:
        """Clear the stored email search results."""
        with self._lock:
            self._last_email_search_results = []

    def set_last_email(self, email: dict) -> None:
        """Store the last email for follow-up actions (like replying).
        
        This enables follow-ups like "reply to this" or "respond" without
        re-specifying the email.
        
        Parameters
        ----------
        email : dict
            Email dict with fields like 'from', 'subject', 'body', 'id', etc.
        """
        if not hasattr(self, '_last_email'):
            self._last_email = None
        with self._lock:
            self._last_email = email.copy() if email else None
            log.debug("Memory: last_email set (from: %s)", email.get("from", "?") if email else "None")

    def get_last_email(self) -> Optional[dict]:
        """Return the last email referenced (None if none stored)."""
        if not hasattr(self, '_last_email'):
            self._last_email = None
        with self._lock:
            return dict(self._last_email) if self._last_email else None

    # ── Auto-extraction ────────────────────────────────────────────────────

    def extract_and_store(self, user_input: str) -> dict[str, str]:
        """Parse *user_input* for common fact patterns and store them.

        Returns a dict of newly stored facts (may be empty).
        Patterns are evaluated in order; the first match for each key wins
        so more-specific patterns must appear before generic ones.
        """
        found: dict[str, str] = {}
        text = user_input.strip()
        for pattern, key in _FACT_PATTERNS:
            if key in found:
                # Already matched a more-specific pattern for this key — skip
                continue
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                value = m.group(1).strip().rstrip(".,!?")
                found[key] = value

        for key, value in found.items():
            self.store(key, value)
        return found

    # ── Prompt injection ───────────────────────────────────────────────────

    def build_messages(
        self,
        system_prompt: str,
        user_query: str,
        include_history: int = 6,
    ) -> list[dict]:
        """Build an Ollama-ready ``messages`` list with memory injected.

        Structure::

            [
              {"role": "system", "content": "<system_prompt> + memory note"},
              ... last N history turns ...
              {"role": "user", "content": user_query},
            ]
        """
        facts = self.list_facts()
        memory_note = ""
        if facts:
            lines = "\n".join(f"  {k}: {v}" for k, v in facts.items())
            memory_note = f"\n\nKnown facts about the user:\n{lines}"

        messages: list[dict] = [
            {"role": "system", "content": system_prompt + memory_note}
        ]

        if include_history > 0:
            messages.extend(self.get_history(last_n=include_history))

        messages.append({"role": "user", "content": user_query})
        return messages

    def facts_summary(self) -> str:
        """Return a readable summary of stored facts for display.

        Returns an empty string when no facts are stored so callers can
        safely do ``if memory.facts_summary(): print(...)``.
        """
        facts = self.list_facts()
        if not facts:
            return ""
        return "\n".join(f"  {k}: {v}" for k, v in facts.items())

    # ── Persistence ────────────────────────────────────────────────────────

    def _persist(self) -> None:
        if not self._persist_path:
            return
        path = Path(self._persist_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(
                    {"facts": self._facts, "history": self._history},
                    fh, indent=2,
                )
        except Exception as exc:
            log.warning("Could not persist memory: %s", exc)

    def _load(self) -> None:
        try:
            with Path(self._persist_path).open(encoding="utf-8") as fh:
                data = json.load(fh)
            self._facts = data.get("facts", {})
            self._history = data.get("history", [])
            log.info("Conversation memory loaded from %s", self._persist_path)
        except Exception as exc:
            log.warning("Could not load persisted memory: %s", exc)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ConversationMemory("
            f"facts={len(self._facts)}, history={len(self._history)})"
        )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

def _make_default_memory() -> ConversationMemory:
    try:
        from configs.settings import settings
        persist = settings.project_root / "data" / "memory.json"
    except Exception:
        from pathlib import Path as _Path
        persist = _Path(__file__).parent.parent / "data" / "memory.json"
    return ConversationMemory(max_history=30, persist_path=persist)


conversation_memory = _make_default_memory()
