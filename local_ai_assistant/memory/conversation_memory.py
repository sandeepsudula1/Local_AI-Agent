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

# Patterns for "remember that my X is Y" / "my favorite X is Y"
# Key is derived from the subject before "is".
_REMEMBER_PATTERNS: list[re.Pattern] = [
    # "remember that my favorite language is Python"
    re.compile(
        r"\b(?:remember\s+(?:that\s+)?|note\s+(?:that\s+)?|store\s+(?:that\s+)?|"
        r"save\s+(?:that\s+)?|keep\s+in\s+mind\s+(?:that\s+)?)"
        r"my\s+(?:favorite\s+|favourite\s+|preferred\s+|fav\s+)?"
        r"(?P<key>[a-z][a-z0-9\s\-_]{1,30}?)"
        r"\s+is\s+(?P<value>.{1,60})",
        re.IGNORECASE,
    ),
    # "my favorite language is Python"
    re.compile(
        r"\bmy\s+(?:favorite|favourite|preferred|fav)\s+"
        r"(?P<key>[a-z][a-z0-9\s\-_]{1,30}?)"
        r"\s+is\s+(?P<value>.{1,60})",
        re.IGNORECASE,
    ),
    # "remember that X is Y"  (no "my" keyword, generic key=value)
    re.compile(
        r"\b(?:remember\s+(?:that\s+)?|note\s+(?:that\s+)?)"
        r"(?P<key>[a-z][a-z0-9\s\-_]{1,30}?)"
        r"\s+is\s+(?P<value>.{1,60})",
        re.IGNORECASE,
    ),
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
        self._last_user_intent_topic: Optional[str] = None
        self._last_general_topic: Optional[str] = None
        self._last_folder: Optional[str] = None
        self._pending_query: Optional[str] = None  # query awaiting folder clarification
        self._last_email_search_results: list[dict] = []  # email search context for reply generation
        # File index: session-only, not persisted.  Maps filename.lower() → full path.
        # Populated whenever a folder is granted / indexed.
        self._file_index: dict[str, str] = {}
        # Lightweight pattern memory: last 50 (query, intent) observations.
        # Used to boost intent classification for recurring query patterns.
        self._user_patterns: list[dict] = []
        # Pending file selection: stored when the system displays a numbered file
        # list and waits for the user to pick one.  Never persisted.
        self._pending_file_selection: list[dict] = []
        self._pending_file_query: str = ""
        # Last explicitly selected or resolved file (full absolute path).
        # Set when the user picks from a disambiguation list or when strict file
        # mode auto-resolves to a single match.  Enables anaphora follow-ups
        # such as "open it" / "summarize that file".  Session-only, never persisted.
        self._selected_file: str = ""
        # Pending documents: the last set of documents returned to the user by
        # a FILE_SEARCH or FILE_LIST handler.  Enables resolution of "above
        # document", "that file", "summarize the previous results" etc.
        self._pending_documents: list[dict] = []
        self._last_response: Optional[str] = None
        self._active_email: Optional[dict] = None  # PART 4: EMAIL CONTEXT LOCK

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

    def recall_for_query(self, query: str) -> Optional[str]:
        """Try to answer a recall query by scanning stored facts.

        Scoring uses three layers:
        1. Exact word overlap between query words and key words.
        2. Stem overlap (first 4 chars) — "preferred" matches "preference".
        3. Value relevance — if the stored value appears in the query.

        Returns a human-readable answer string, or None when no relevant fact
        is found.
        """
        with self._lock:
            if not self._facts:
                return None
            q_lower = query.lower()
            q_words = set(re.sub(r"[^a-z0-9\s]", " ", q_lower).split())
            scored: list[tuple[float, str, str]] = []
            for key, value in self._facts.items():
                key_words = set(re.sub(r"[_\-]", " ", key).split())
                # 1. Exact word overlap
                exact = float(len(key_words & q_words))
                # 2. Stem overlap (4-char prefix match, skip exact-match pairs)
                stem = sum(
                    0.5
                    for kw in key_words
                    for qw in q_words
                    if len(kw) >= 4 and len(qw) >= 4
                    and kw[:4] == qw[:4] and kw != qw
                )
                # 3. Value appears in query (e.g. "Python" in "what python version")
                val_words = set(re.sub(r"[^a-z0-9\s]", " ", value.lower()).split())
                val_hit = 0.3 if q_words & val_words else 0.0
                total = exact + stem + val_hit
                if total > 0:
                    scored.append((total, key, value))
            if not scored:
                return None
            scored.sort(key=lambda x: -x[0])
            _, best_key, best_val = scored[0]
            display_key = best_key.replace("_", " ").replace("-", " ")
            return f"Your {display_key} is **{best_val}**."

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
            self._file_index.clear()
            self._user_patterns.clear()
            self._pending_file_selection.clear()
            self._pending_file_query = ""
            self._selected_file = ""
            self._persist()
        log.info("Conversation memory cleared")

    # ── File index (session-only) ─────────────────────────────────────────

    def register_file(self, filename: str, full_path: str) -> None:
        """Register a single file in the session file index.

        Parameters
        ----------
        filename : str
            The bare filename (e.g. ``"AiAgent.txt"``).  Stored lower-cased
            so lookups are always case-insensitive.
        full_path : str
            The absolute path to the file on disk.
        """
        with self._lock:
            self._file_index[filename.lower().strip()] = full_path
            log.debug("File index: registered %r -> %r", filename, full_path)

    def register_folder_files(self, folder_path: str) -> int:
        """Scan *folder_path* and register all files found (non-recursive).

        Returns the number of files newly registered.  Safe to call on
        folders that don't exist — returns 0 with no exception.
        """
        import os
        count = 0
        try:
            for entry in os.scandir(folder_path):
                if entry.is_file():
                    self.register_file(entry.name, entry.path)
                    count += 1
            log.info("File index: registered %d files from %r", count, folder_path)
        except OSError as exc:
            log.debug("register_folder_files(%r) failed: %s", folder_path, exc)
        return count

    def lookup_file(self, query: str) -> Optional[str]:
        """Return the full path of the first registered file whose name appears in *query*.

        Lookup is case-insensitive.  Returns ``None`` when no registered filename
        is a substring of the lowercased query.
        """
        query_lower = query.lower()
        with self._lock:
            for name, path in self._file_index.items():
                if name in query_lower:
                    log.debug("File index: lookup hit %r -> %r", name, path)
                    return path
        return None

    def get_file_index(self) -> dict[str, str]:
        """Return a copy of the current file index (filename.lower → full path)."""
        with self._lock:
            return dict(self._file_index)

    # ── Pending file selection (session-only) ────────────────────────────────

    def set_pending_file_selection(
        self, files: list[dict], original_query: str = ""
    ) -> None:
        """Store a numbered file list waiting for the user to choose one.

        Parameters
        ----------
        files : list[dict]
            The candidate files shown to the user (each must have ``path``).
        original_query : str
            The query that triggered the file search (stored so the
            post-selection handler can pick the right action).
        """
        with self._lock:
            self._pending_file_selection = list(files)
            self._pending_file_query = original_query
            log.debug(
                "Memory: pending_file_selection set (%d files, query=%r)",
                len(files), original_query[:60],
            )

    def get_pending_file_selection(self) -> list[dict]:
        """Return the pending candidate file list (empty list if none)."""
        with self._lock:
            return list(self._pending_file_selection)

    def get_pending_file_query(self) -> str:
        """Return the original query that triggered the pending file search."""
        with self._lock:
            return self._pending_file_query

    def clear_pending_file_selection(self) -> None:
        """Clear the pending file selection state."""
        with self._lock:
            self._pending_file_selection.clear()
            self._pending_file_query = ""
            log.debug("Memory: pending_file_selection cleared")

    def has_pending_file_selection(self) -> bool:
        """Return True when a file list is waiting for user selection."""
        with self._lock:
            return bool(self._pending_file_selection)

    # ── Selected file (session-only) ─────────────────────────────────────────

    def set_selected_file(self, path: str) -> None:
        """Record the last file resolved or chosen by the user.

        Enables anaphora follow-ups ("open it", "summarize that file") by
        persisting the resolved path for the lifetime of the session.
        Session-only — never written to the JSON persist file.
        """
        with self._lock:
            self._selected_file = path
            log.debug("Memory: selected_file set to %r", path)

    def get_selected_file(self) -> str:
        """Return the last selected/resolved file path, or empty string."""
        with self._lock:
            return self._selected_file

    def clear_selected_file(self) -> None:
        """Clear the selected file state."""
        with self._lock:
            self._selected_file = ""

    def set_last_user_intent_topic(self, topic: str) -> None:
        """Store the ongoing conversation topic for follow-up resolution."""
        with self._lock:
            if topic:
                self._last_user_intent_topic = topic

    def get_last_user_intent_topic(self) -> Optional[str]:
        """Get the stored ongoing conversation topic."""
        with self._lock:
            return self._last_user_intent_topic

    def set_last_general_topic(self, topic: str) -> None:
        """Store the last topic discussed in GENERAL mode."""
        with self._lock:
            if topic:
                self._last_general_topic = topic

    def get_last_general_topic(self) -> Optional[str]:
        """Get the last topic discussed in GENERAL mode."""
        with self._lock:
            return getattr(self, '_last_general_topic', None)

    # ── Pending documents (session-only) ──────────────────────────────────

    def set_pending_documents(self, docs: list[dict]) -> None:
        """Store the last set of documents returned to the user.

        Each dict should contain at least ``{'path': '...', 'name': '...'}``.
        Enables resolution of 'above document', 'that file', etc.
        """
        with self._lock:
            self._pending_documents = list(docs) if docs else []
            log.debug("Memory: pending_documents set (%d items)", len(self._pending_documents))

    def get_pending_documents(self) -> list[dict]:
        """Return the last set of documents shown to the user."""
        with self._lock:
            return list(self._pending_documents)

    def clear_pending_documents(self) -> None:
        """Clear the pending documents list."""
        with self._lock:
            self._pending_documents = []

    # ── Last response context (session-only) ──────────────────────────────

    def set_last_response(self, response: str) -> None:
        """Store the last assistant response for contextual follow-ups."""
        with self._lock:
            self._last_response = response
            log.debug("Memory: last_response set (len=%d)", len(response))

    def get_last_response(self) -> Optional[str]:
        """Return the last assistant response, or None."""
        with self._lock:
            return self._last_response

    # ── Pattern memory (session-only) ─────────────────────────────────────

    def record_pattern(self, query: str, intent: str) -> None:
        """Save a (query, intent) observation for future boosting.

        Trivial intents (GENERAL, CHAT, GREETING, TIME, DATE) are ignored to
        keep the pattern list focused on actionable domain intents.
        Capped at the 50 most-recent observations.
        """
        if intent in {"GENERAL", "CHAT", "GREETING", "TIME", "DATE", "EMPTY",
                      "UNKNOWN", "ACCESS_CONTROL", "PERMISSION_GRANTED",
                      "PERMISSION_DENIED", "NO_PENDING_PERMISSION"}:
            return
        with self._lock:
            self._user_patterns.append({"query": query[:200], "intent": intent})
            if len(self._user_patterns) > 50:
                self._user_patterns = self._user_patterns[-50:]

    def get_recent_patterns(self, n: int = 10) -> list[dict]:
        """Return the *n* most-recent (query, intent) observations."""
        with self._lock:
            return list(self._user_patterns[-n:])

    def get_boosted_intent(self, query: str) -> Optional[str]:
        """Suggest an intent based on overlap with past successful queries.

        Uses simple word-overlap scoring over the last 20 patterns.
        Returns ``None`` when no pattern has at least 2 words in common with
        *query* — avoids spurious boosts on very short or unrelated inputs.
        """
        query_lower = query.lower()
        q_words = set(query_lower.split())
        with self._lock:
            patterns = list(self._user_patterns[-20:])

        scores: dict[str, int] = {}
        for p in patterns:
            p_words = set(p["query"].lower().split())
            overlap = len(q_words & p_words)
            if overlap >= 2:
                intent = p["intent"]
                scores[intent] = scores.get(intent, 0) + overlap

        if not scores:
            return None
        return max(scores, key=lambda k: scores[k])

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

    # ── PART 4: EMAIL CONTEXT LOCK ──────────────────────────────────────────

    def set_active_email(self, email_data: dict) -> None:
        """Lock an email as the active context for follow-up queries."""
        with self._lock:
            self._active_email = email_data.copy() if email_data else None
            log.info("Memory: active_email locked context: %s", 
                     email_data.get("subject") if email_data else "None")

    def get_active_email(self) -> Optional[dict]:
        """Return the locked active email context, if any."""
        with self._lock:
            return self._active_email.copy() if self._active_email else None

    def clear_active_email(self) -> None:
        """Unlock the active email context."""
        with self._lock:
            self._active_email = None

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

        # Also run the "remember that my X is Y" / "my favorite X is Y" patterns.
        # These derive the key from the matched subject so they handle arbitrary facts.
        for pat in _REMEMBER_PATTERNS:
            m = pat.search(text)
            if m:
                raw_key = m.group("key").strip().rstrip(".,!?").lower()
                raw_val = m.group("value").strip().rstrip(".,!?")
                # Normalise key: collapse spaces to underscore, strip trailing fillers
                norm_key = re.sub(r"\s+", "_", raw_key)
                norm_key = re.sub(r"_(is|are|was)$", "", norm_key)
                if norm_key and raw_val and norm_key not in found:
                    found[norm_key] = raw_val
                    break  # first matching remember-pattern wins

        for key, value in found.items():
            self.store(key, value)
        return found

    # ── Prompt injection ───────────────────────────────────────────────────

    @staticmethod
    def _clean_history(turns: list[dict]) -> list[dict]:
        """Filter *turns* keeping only clean conversational messages.

        Removes assistant turns whose content looks like a system/agent pipeline
        output (email notices, reminder confirmations, document results, debug logs)
        so they never appear in the LLM's context window.

        User turns are always kept unchanged.  Safe to call on an empty list.
        """
        import re

        _NOISE_PATTERNS_COMPILED = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in [
                r"No draft email",
                r"First generate a reply",
                r"search for (?:emails?|ideas)",
                r"Searching emails",
                r"Fetching latest emails",
                r"No emails found",
                r"Found \d+ email",
                r"Reminder (?:set|canceled|deleted|not found)",
                r"I could not understand the reminder",
                r"No reminders",
                r"No documents found",
                r"No relevant information found",
                r"I cannot access information from",
                r"Access request denied",
                r"There is no pending permission",
                r"\[Email\]",
                r"\[Reminder\]",
                r"\[Info\]",
                r"\[Warning\]",
                r"\[Ready\]",
                r"\[MCP\]",
                r"Planner Decision:",
                r"^Assistant:",
                r"_\(no response\)_",
            ]
        ]
        _NOISE_PREFIXES = (
            "No draft email", "no draft email",
            "First generate a reply", "first generate a reply",
            "Found ", "No emails found",
            "Reminder set", "Reminder canceled", "Reminder deleted",
            "No documents found", "No relevant information",
            "I cannot access information from",
            "Access request denied",
            "There is no pending permission",
            "[Error]", "[Warning]", "_(no response)_",
        )
        _NOISE_EMOJI = ("\u26a0\ufe0f", "\u274c", "\u23f0", "\u2705 Access")

        clean: list[dict] = []
        for turn in turns:
            role = turn.get("role", "")
            content = (turn.get("content") or "").strip()

            # Always keep user turns — they are raw input, always clean
            if role == "user":
                clean.append(turn)
                continue

            if role == "assistant":
                if not content:
                    continue
                if any(content.startswith(p) for p in _NOISE_EMOJI):
                    continue
                if any(content.startswith(p) for p in _NOISE_PREFIXES):
                    continue
                if any(pat.search(content) for pat in _NOISE_PATTERNS_COMPILED):
                    continue
                # Reject if 60%+ of lines look like structured list items
                lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
                if len(lines) >= 3:
                    structured = sum(
                        1 for ln in lines
                        if re.match(r'^(\d+\.|[-*]|\[\d+\])', ln)
                    )
                    if structured / len(lines) >= 0.6:
                        continue
                clean.append(turn)

        return clean

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
              ... last N history turns (noise-filtered) ...
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
            raw_history = self.get_history(last_n=include_history)
            # Strip system/agent noise before passing history to the LLM
            messages.extend(self._clean_history(raw_history))

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
        from configs.settings import DATA_DIR
        persist = DATA_DIR / "memory.json"
    except Exception:
        from pathlib import Path as _Path
        persist = _Path(__file__).parent.parent / "data" / "memory.json"
    return ConversationMemory(max_history=30, persist_path=persist)


conversation_memory = _make_default_memory()
