"""
core/router.py
==============
Maps an intent label produced by IntentClassifier → a canonical tool name
registered in ``tools/tool_registry.TOOLS``.

Design notes
------------
* If an intent has no matching tool (CHAT, GREETING, GENERAL, TIME, DATE)
  the router returns ``None`` — the pipeline should fall through to a
  direct LLM call without invoking any tool.
* ``Router`` wraps ``ToolCatalog.for_intent()`` with a hard-coded
  fallback table so the two sources stay in sync.
* ``Router.route()`` is deterministic and does not call any LLM.
"""

from __future__ import annotations

from typing import Optional

from core.logging_config import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Static fallback table
# Matches the ``intent`` field in tools/tool_registry.TOOLS but lives here
# so the router works even if tool_registry fails to import.
# ---------------------------------------------------------------------------

_INTENT_TOOL_MAP: dict[str, str] = {
    # Document tools
    "RETRIEVAL":            "documents.search",
    "DOCUMENT_SEARCH":      "documents.search",
    "DOCUMENT_FOLDER_QUERY": "documents.search",
    "SUMMARY":              "documents.summarize",
    "DOCUMENT_SUMMARY":     "documents.summarize",
    "DOCUMENT_LIST":        "documents.list",
    "TOPIC":                "documents.topics",
    "TOPICS":               "documents.topics",
    "DOCUMENT_TOPICS":      "documents.topics",
    "COMPARISON":           "system.compare",

    # Email tools
    "EMAIL_SEARCH":         "email.search",
    "EMAIL_QUERY":          "email.query",
    "EMAIL":                "email.search",
    "EMAIL_SUMMARY":        "email.summarize",
    "EMAIL_SUMMARIZE":      "email.summarize",
    "EMAIL_REPLY":          "email.reply",
    "EMAIL_SEND":           "email.send",

    # Audio tools
    "AUDIO_TRANSCRIBE":     "audio.transcribe",
    "TRANSCRIPTION":        "audio.transcribe",
    "AUDIO_QUERY":          "audio.query",
    "AUDIO_SEARCH":         "audio.query",
    "AUDIO_LIST":           "audio.list",

    # Reminder tools
    "REMINDER":             "reminders.set",
    "SET_REMINDER":         "reminders.set",
    "REMINDERS_SET":        "reminders.set",
    "LIST_REMINDERS":       "reminders.list",
    "REMINDERS_LIST":       "reminders.list",
    "DELETE_REMINDER":      "reminders.delete",
    "REMINDERS_DELETE":     "reminders.delete",

    # System / notification
    "NOTIFICATION":         "system.chat",
    "SYSTEM":               "system.chat",
}

# Intents that go directly to the LLM without any tool
_LLM_ONLY_INTENTS: frozenset[str] = frozenset({
    "CHAT",
    "GENERAL",
    "GREETING",
    "TIME",
    "DATE",
    "HELP",
    "UNKNOWN",
})


class Router:
    """Maps intent labels to tool names.

    Usage::

        router = Router()
        tool_name = router.route("EMAIL_SEARCH")  # → "email.search"
        tool_name = router.route("CHAT")          # → None  (LLM only)
    """

    def __init__(self) -> None:
        self._map = dict(_INTENT_TOOL_MAP)

        # Merge with ToolCatalog mappings so they stay consistent
        try:
            from tools.tool_registry import tool_catalog
            for tool_name, meta in tool_catalog._tools.items():
                intent = meta.get("intent", "").upper()
                if intent and tool_name and intent not in self._map:
                    self._map[intent] = tool_name
        except Exception as exc:
            log.debug("Router: could not merge ToolCatalog mappings — %s", exc)

    def route(self, intent: str) -> Optional[str]:
        """Return the tool name for *intent*, or ``None`` for LLM-only paths."""
        key = (intent or "").strip().upper()
        if key in _LLM_ONLY_INTENTS:
            return None
        tool = self._map.get(key)
        if tool is None:
            log.debug("Router: no tool mapped for intent %r — defaulting to LLM", key)
        return tool

    def is_llm_only(self, intent: str) -> bool:
        """Return ``True`` if *intent* requires no tool call."""
        return (intent or "").strip().upper() in _LLM_ONLY_INTENTS

    def available_mappings(self) -> dict[str, str]:
        """Return a snapshot of all intent → tool_name mappings."""
        return dict(self._map)


# Module-level singleton
router = Router()
