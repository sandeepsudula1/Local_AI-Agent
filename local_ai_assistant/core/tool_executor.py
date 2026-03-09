"""
core/tool_executor.py
=====================
Executes a named tool and returns a uniform ``ToolResult`` so the pipeline
never needs to know which agent or function backs each tool.

Tool map (tool_name → handler)
------------------------------
  documents.search    → agents/knowledge/retrieval_agent.py
  documents.summarize → agents/knowledge/summary_agent.py
  documents.list      → agents/knowledge/document_list_agent.py
  documents.topics    → agents/knowledge/topic_agent.py
  email.search        → agents/knowledge/email_query_agent.py
  email.summarize     → agents/knowledge/email_summarizer_agent.py
  audio.transcribe    → agents/knowledge/audio_agent.py  (transcribe)
  audio.query         → agents/knowledge/audio_agent.py  (query)
  audio.list          → agents/knowledge/audio_agent.py  (list)
  reminders.set       → agents/tasks/reminder_agent.py
  reminders.list      → agents/tasks/reminder_agent.py
  reminders.delete    → agents/tasks/reminder_agent.py
  system.chat         → direct LLM (handled by pipeline; executor returns None)
  system.compare      → agents/knowledge/retrieval_agent.py (comparison mode)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Structured result from a single tool execution."""
    tool_name: str
    success: bool
    output: Optional[str] = None
    source: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success and bool(self.output)


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------

def _handle_document_search(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.retrieval_agent import handle_retrieval
    answer = handle_retrieval(user_input)
    return answer, ""


def _handle_document_summarize(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.summary_agent import handle_summary
    answer = handle_summary(user_input)
    return answer, ""


def _handle_document_list(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.document_list_agent import handle_document_list
    answer = handle_document_list(user_input)
    return answer, ""


def _handle_document_topics(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.topic_agent import handle_topic
    answer = handle_topic(user_input)
    return answer, ""


def _handle_email_search(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.email_query_agent import handle_email_query
    answer = handle_email_query(user_input)
    return answer, ""


def _handle_email_summarize(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.email_summarizer_agent import handle_email_summarizer
    answer = handle_email_summarizer(user_input)
    return answer, ""


def _handle_audio_transcribe(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.audio_agent import handle_audio_transcription
    answer = handle_audio_transcription(user_input)
    return answer, ""


def _handle_audio_query(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.audio_agent import handle_audio_query
    answer = handle_audio_query(user_input)
    return answer, ""


def _handle_audio_list(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.audio_agent import handle_audio_list
    answer = handle_audio_list(user_input)
    return answer, ""


def _handle_reminder_set(user_input: str, **ctx) -> tuple[str, str]:
    from agents.tasks.reminder_agent import handle_set_reminder
    answer = handle_set_reminder(user_input)
    return answer, ""


def _handle_reminder_list(user_input: str, **ctx) -> tuple[str, str]:
    from agents.tasks.reminder_agent import handle_list_reminders
    answer = handle_list_reminders(user_input)
    return answer, ""


def _handle_reminder_delete(user_input: str, **ctx) -> tuple[str, str]:
    from agents.tasks.reminder_agent import handle_delete_reminder
    answer = handle_delete_reminder(user_input)
    return answer, ""


def _handle_system_compare(user_input: str, **ctx) -> tuple[str, str]:
    # Comparison reuses the retrieval agent with a special note
    from agents.knowledge.retrieval_agent import handle_retrieval
    answer = handle_retrieval(user_input)
    return answer, ""


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "documents.search":     _handle_document_search,
    "documents.summarize":  _handle_document_summarize,
    "documents.list":       _handle_document_list,
    "documents.topics":     _handle_document_topics,
    "email.search":         _handle_email_search,
    "email.summarize":      _handle_email_summarize,
    "audio.transcribe":     _handle_audio_transcribe,
    "audio.query":          _handle_audio_query,
    "audio.list":           _handle_audio_list,
    "reminders.set":        _handle_reminder_set,
    "reminders.list":       _handle_reminder_list,
    "reminders.delete":     _handle_reminder_delete,
    "system.compare":       _handle_system_compare,
}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Runs named tools and wraps results in ``ToolResult``.

    Usage::

        executor = ToolExecutor()
        result = executor.execute("email.search", "find emails from Alice")
        if result:
            print(result.output)
        else:
            print("Tool failed:", result.error)
    """

    def execute(
        self,
        tool_name: str,
        user_input: str,
        **ctx: Any,
    ) -> ToolResult:
        """Execute *tool_name* with *user_input* and return a ``ToolResult``.

        Parameters
        ----------
        tool_name:
            Canonical tool name from ``tools/tool_registry.TOOLS``.
        user_input:
            Raw user query / instruction.
        **ctx:
            Additional context forwarded to the handler (e.g. ``history``).

        Returns
        -------
        ``ToolResult`` — ``success=False`` when the tool is unknown or raises.
        """
        if not tool_name:
            return ToolResult(
                tool_name="",
                success=False,
                error="No tool name provided.",
            )

        handler = _HANDLERS.get(tool_name)
        if handler is None:
            log.warning("ToolExecutor: unknown tool %r", tool_name)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Unknown tool: {tool_name!r}",
            )

        t0 = time.perf_counter()
        try:
            output, source = handler(user_input, **ctx)
            latency = (time.perf_counter() - t0) * 1000
            log.info(
                "ToolExecutor: tool=%r latency=%.0fms ok=%s",
                tool_name, latency, bool(output),
            )
            return ToolResult(
                tool_name=tool_name,
                success=bool(output),
                output=output or None,
                source=source or None,
                latency_ms=latency,
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            log.exception("ToolExecutor: tool=%r raised: %s", tool_name, exc)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                latency_ms=latency,
            )

    @staticmethod
    def available_tools() -> list[str]:
        """Return the list of tool names this executor knows about."""
        return list(_HANDLERS.keys())


# Module-level singleton
tool_executor = ToolExecutor()
