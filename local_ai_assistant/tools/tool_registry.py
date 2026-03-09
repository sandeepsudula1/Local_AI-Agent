"""
tools/tool_registry.py
=======================
Human-readable catalogue of all MCP tools available in the assistant.

Two uses
---------
1. **Anti-hallucination guard** — the agent checks this registry before
   invoking a tool so it never invents tool names.

2. **Discovery** — when the user asks "what tools are available?" the
   ``ToolCatalog`` formats the registry contents into a readable answer.

Structure
---------
Each entry has:
  - ``description`` — one-line purpose
  - ``examples``    — example user queries that trigger this tool
  - ``intent``      — the planner intent that maps to this tool
  - ``args``        — public argument names

Usage::

    from tools.tool_registry import tool_catalog

    print(tool_catalog.describe_all())          # formatted list
    print(tool_catalog.validate("email.search")) # True
    print(tool_catalog.for_intent("RETRIEVAL"))  # "documents.search"
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Master catalogue  — add new tools here only
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict] = {
    "documents.search": {
        "description": "Search indexed local documents (PDFs, CSVs, images) for a specific query.",
        "examples": [
            "how many employees in 2024",
            "what is the company revenue",
            "what skills were developed during the internship",
        ],
        "intent": "RETRIEVAL",
        "args": ["query: str"],
    },
    "documents.summarize": {
        "description": "Generate a structured summary of all indexed documents.",
        "examples": [
            "summarize the documents",
            "give me an overview of the knowledge base",
            "what are the key points across all files",
        ],
        "intent": "SUMMARY",
        "args": [],
    },
    "documents.list": {
        "description": "List all available document files in the knowledge base.",
        "examples": [
            "list all documents",
            "what files do I have",
            "show available documents",
        ],
        "intent": "DOCUMENT_LIST",
        "args": [],
    },
    "documents.topics": {
        "description": "Extract and list the main topics or themes across all documents.",
        "examples": [
            "what topics are covered",
            "what themes are in the documents",
            "main subjects in the knowledge base",
        ],
        "intent": "TOPIC",
        "args": [],
    },
    "email.search": {
        "description": "Search or filter emails by subject, sender, date, or keyword.",
        "examples": [
            "find emails from Alice",
            "search emails about invoice",
            "emails received last week",
        ],
        "intent": "EMAIL_SEARCH",
        "args": ["query: str"],
    },
    "email.summarize": {
        "description": "Summarize the most recent emails in the inbox.",
        "examples": [
            "summarize my emails",
            "inbox summary",
            "what are my latest emails about",
        ],
        "intent": "EMAIL_SUMMARY",
        "args": [],
    },
    "audio.transcribe": {
        "description": "Transcribe or index an audio file (.mp3, .wav, .m4a).",
        "examples": [
            "transcribe meeting.mp3",
            "index audio recording",
            "add voice_note.m4a to the knowledge base",
        ],
        "intent": "AUDIO_TRANSCRIBE",
        "args": ["file_path: str"],
    },
    "audio.query": {
        "description": "Ask a question about transcribed audio content.",
        "examples": [
            "what was discussed in the meeting recording",
            "who spoke about the budget in the audio",
            "summarize the voice note",
        ],
        "intent": "AUDIO_QUERY",
        "args": ["query: str"],
    },
    "audio.list": {
        "description": "List all indexed audio files.",
        "examples": [
            "list audio files",
            "show indexed recordings",
            "what audio files are available",
        ],
        "intent": "AUDIO_LIST",
        "args": [],
    },
    "reminders.set": {
        "description": "Create a new timed reminder or alarm.",
        "examples": [
            "remind me at 15:30 to call Alice",
            "set a reminder for tomorrow at 9am",
            "alert me in 10 minutes",
        ],
        "intent": "REMINDER_SET",
        "args": ["text: str", "time: str"],
    },
    "reminders.list": {
        "description": "List all pending reminders.",
        "examples": [
            "show my reminders",
            "what reminders do I have",
            "list all alarms",
        ],
        "intent": "REMINDER_LIST",
        "args": [],
    },
    "reminders.delete": {
        "description": "Delete a specific reminder by name or identifier.",
        "examples": [
            "delete the reminder for Alice",
            "remove reminder 2",
            "cancel my 3pm alarm",
        ],
        "intent": "REMINDER_DELETE",
        "args": ["identifier: str"],
    },
    "system.chat": {
        "description": "Respond to general conversational or knowledge questions.",
        "examples": [
            "tell me a joke",
            "what is machine learning",
            "who invented Python",
        ],
        "intent": "CHAT",
        "args": ["query: str"],
    },
    "system.compare": {
        "description": "Generate a structured comparison of two technologies, concepts, or items.",
        "examples": [
            "Python vs Java",
            "compare Node.js and Deno",
            "which is better, React or Vue",
        ],
        "intent": "COMPARE",
        "args": ["query: str"],
    },
}


# ---------------------------------------------------------------------------
# ToolCatalog
# ---------------------------------------------------------------------------

class ToolCatalog:
    """Read-only view over the TOOLS registry."""

    # ── lookup ──────────────────────────────────────────────────────────────

    def validate(self, tool_name: str) -> bool:
        """Return True if *tool_name* is a registered tool."""
        return tool_name in TOOLS

    def get(self, tool_name: str) -> Optional[dict]:
        """Return the tool definition dict, or None."""
        return TOOLS.get(tool_name)

    def for_intent(self, intent: str) -> Optional[str]:
        """Return the tool name that handles *intent*, or None."""
        for name, spec in TOOLS.items():
            if spec.get("intent") == intent.upper():
                return name
        return None

    def list_tools(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(TOOLS.keys())

    # ── formatting ───────────────────────────────────────────────────────────

    def describe(self, tool_name: str) -> str:
        """Return a one-line description of *tool_name*."""
        spec = TOOLS.get(tool_name)
        if not spec:
            return f"Unknown tool: {tool_name}"
        return f"{tool_name}: {spec['description']}"

    def describe_all(self) -> str:
        """Return a formatted multi-line catalogue for display or LLM injection."""
        lines = ["Available tools:", ""]
        for name in self.list_tools():
            spec = TOOLS[name]
            lines.append(f"  {name}")
            lines.append(f"    {spec['description']}")
            if spec.get("examples"):
                examples = "; ".join(spec["examples"][:2])
                lines.append(f"    e.g. {examples}")
            lines.append("")
        return "\n".join(lines)

    def describe_for_llm(self) -> str:
        """Compact version suitable for injection into an LLM system prompt."""
        parts = [f"{n}: {d['description']}" for n, d in TOOLS.items()]
        return "Tools:\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
tool_catalog = ToolCatalog()
