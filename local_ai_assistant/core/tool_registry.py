"""
core/tool_registry.py
=====================
Central registry for all tools the assistant can invoke.

A *tool* is any callable that the orchestrator can dispatch to.
Tools are registered with a string key (e.g. ``"documents.search"``) and
can carry metadata used for logging, health-checks, and documentation.

Design
------
- ``ToolRegistry`` is a simple dict-backed registry.
- The global singleton ``tool_registry`` is pre-populated once by
  ``_register_defaults()`` at import time.
- New tools can be added at runtime via ``tool_registry.register(...)``.

Usage::

    from core.tool_registry import tool_registry

    # Dispatch a tool by name
    result = tool_registry.call("documents.search", query="employees 2024")

    # List all registered tool names
    print(tool_registry.list_tools())
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# ToolSpec — metadata for a single tool
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """Descriptor for a registered tool."""

    name: str
    fn: Callable[..., Any]
    description: str = ""
    category: str = "general"
    enabled: bool = True

    # Runtime stats (mutable)
    call_count: int = field(default=0, compare=False)
    error_count: int = field(default=0, compare=False)
    total_latency_ms: float = field(default=0.0, compare=False)

    @property
    def avg_latency_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_latency_ms / self.call_count


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Thread-safe registry of named tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ── registration ────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        description: str = "",
        category: str = "general",
        enabled: bool = True,
    ) -> None:
        """Register a tool under ``name``.

        Parameters
        ----------
        name:
            Dot-namespaced key, e.g. ``"documents.search"``.
        fn:
            The callable to invoke when the tool is used.
        description:
            Human-readable description shown in health checks.
        category:
            Grouping label, e.g. ``"documents"``, ``"reminders"``.
        enabled:
            Set False to disable without unregistering.
        """
        if name in self._tools:
            log.debug("Tool '%s' overwritten in registry", name)
        self._tools[name] = ToolSpec(
            name=name,
            fn=fn,
            description=description,
            category=category,
            enabled=enabled,
        )
        log.debug("Registered tool: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        self._tools.pop(name, None)

    def enable(self, name: str) -> None:
        if name in self._tools:
            self._tools[name].enabled = True

    def disable(self, name: str) -> None:
        if name in self._tools:
            self._tools[name].enabled = False

    # ── lookup ───────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> list[str]:
        """Return sorted list of registered tool names."""
        tools = [
            spec.name for spec in self._tools.values()
            if (category is None or spec.category == category)
        ]
        return sorted(tools)

    def list_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    # ── invocation ───────────────────────────────────────────────────────────

    def call(self, name: str, **kwargs: Any) -> Any:
        """Invoke a registered tool by name.

        Parameters
        ----------
        name:
            Registered tool name.
        **kwargs:
            Passed directly to the tool function.

        Returns
        -------
        Whatever the tool function returns.

        Raises
        ------
        KeyError
            If the tool is not registered.
        RuntimeError
            If the tool is disabled.
        """
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"Tool '{name}' is not registered.")
        if not spec.enabled:
            raise RuntimeError(f"Tool '{name}' is currently disabled.")

        t0 = time.perf_counter()
        try:
            result = spec.fn(**kwargs)
            spec.call_count += 1
            elapsed = (time.perf_counter() - t0) * 1000
            spec.total_latency_ms += elapsed
            log.debug(
                "Tool '%s' completed in %.1f ms",
                name, elapsed,
            )
            return result
        except Exception:
            spec.error_count += 1
            log.exception("Tool '%s' raised an exception", name)
            raise

    # ── health / stats ───────────────────────────────────────────────────────

    def stats(self) -> dict[str, dict]:
        """Return per-tool runtime statistics."""
        return {
            spec.name: {
                "enabled": spec.enabled,
                "calls": spec.call_count,
                "errors": spec.error_count,
                "avg_latency_ms": round(spec.avg_latency_ms, 1),
            }
            for spec in self._tools.values()
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"ToolRegistry(tools={self.list_tools()})"


# ---------------------------------------------------------------------------
# Global singleton + default registration
# ---------------------------------------------------------------------------

tool_registry = ToolRegistry()


def _register_defaults() -> None:
    """Register all built-in tools lazily (avoids circular imports at load time)."""
    try:
        from agents.knowledge.retrieval_agent import handle_retrieval
        from agents.knowledge.summary_agent import handle_summary
        from agents.knowledge.topic_agent import handle_topics
        from agents.knowledge.document_list_agent import list_all_documents
        from agents.core.general_agent import handle_general
        from agents.core.planner_agent import decide_intent

        tool_registry.register(
            "system.chat",
            fn=handle_general,
            description="Free-form LLM conversation",
            category="system",
        )
        tool_registry.register(
            "system.intent",
            fn=decide_intent,
            description="Classify user message intent",
            category="system",
        )
        tool_registry.register(
            "documents.list",
            fn=list_all_documents,
            description="List all documents in the knowledge base",
            category="documents",
        )
        tool_registry.register(
            "documents.summarize",
            fn=handle_summary,
            description="Summarise all indexed documents",
            category="documents",
        )
        tool_registry.register(
            "documents.topics",
            fn=handle_topics,
            description="Extract main topics from indexed documents",
            category="documents",
        )
    except Exception as exc:
        log.warning("Could not register default tools: %s", exc)


def register_retrieval_tool(vector_db: Any, threshold: float, model_name: str) -> None:
    """Register the documents.search tool once the vector DB is ready.

    Called by the vector store service after the DB has finished loading.

    Parameters
    ----------
    vector_db:
        Loaded ChromaDB instance.
    threshold:
        Similarity score threshold for retrieval.
    model_name:
        Ollama model to use for answer synthesis.
    """
    from agents.knowledge.retrieval_agent import handle_retrieval

    tool_registry.register(
        "documents.search",
        fn=functools.partial(
            handle_retrieval,
            vector_db=vector_db,
            threshold=threshold,
            model_name=model_name,
        ),
        description="RAG search over local documents",
        category="documents",
    )
    log.info("Tool 'documents.search' registered with live vector DB")
