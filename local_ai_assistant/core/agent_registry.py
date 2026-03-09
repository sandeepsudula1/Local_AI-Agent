"""
core/agent_registry.py
======================
Registry of all agent handlers available to the orchestrator.

An *agent* in this context is a handler function that maps to a specific
intent (e.g. ``RETRIEVAL`` → ``retrieval_handler``).  The registry
decouples the orchestrator from the concrete handler implementations.

Design
------
- ``AgentRegistry`` maps intent labels → ``AgentSpec`` objects.
- The global ``agent_registry`` singleton is populated at startup.
- Any agent can be swapped out (e.g. for testing) by re-registering.

Usage::

    from core.agent_registry import agent_registry

    handler = agent_registry.get_handler("RETRIEVAL")
    if handler:
        result = handler(user_input="how many employees in 2024?")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# AgentSpec
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    """Descriptor for a registered agent handler."""

    intent: str                     # e.g. "RETRIEVAL"
    handler: Callable[..., Any]     # called with (user_input, **context)
    description: str = ""
    requires_vector_db: bool = False
    fallback_intent: Optional[str] = None  # intent to try if this one fails

    # Runtime counters
    call_count: int = field(default=0, compare=False)
    error_count: int = field(default=0, compare=False)


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """Maps intent labels to agent handler functions."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSpec] = {}
        self._fallback_handler: Optional[Callable[..., Any]] = None

    # ── registration ────────────────────────────────────────────────────────

    def register(
        self,
        intent: str,
        handler: Callable[..., Any],
        *,
        description: str = "",
        requires_vector_db: bool = False,
        fallback_intent: Optional[str] = None,
    ) -> None:
        """Register a handler for ``intent``.

        Parameters
        ----------
        intent:
            Upper-case intent label, e.g. ``"RETRIEVAL"``.
        handler:
            Callable ``(user_input: str, **ctx) -> str``.
        description:
            Human-readable purpose.
        requires_vector_db:
            If True the orchestrator will check vector DB readiness first.
        fallback_intent:
            Intent to re-route to if this handler returns ``None``.
        """
        intent = intent.upper()
        self._agents[intent] = AgentSpec(
            intent=intent,
            handler=handler,
            description=description,
            requires_vector_db=requires_vector_db,
            fallback_intent=fallback_intent,
        )
        log.debug("Registered agent handler for intent: %s", intent)

    def register_fallback(self, handler: Callable[..., Any]) -> None:
        """Register a catch-all handler used when no intent matches."""
        self._fallback_handler = handler
        log.debug("Registered fallback agent handler")

    # ── lookup ───────────────────────────────────────────────────────────────

    def get_spec(self, intent: str) -> Optional[AgentSpec]:
        return self._agents.get(intent.upper())

    def get_handler(self, intent: str) -> Optional[Callable[..., Any]]:
        spec = self.get_spec(intent)
        return spec.handler if spec else None

    def get_fallback(self) -> Optional[Callable[..., Any]]:
        return self._fallback_handler

    def list_intents(self) -> list[str]:
        return sorted(self._agents.keys())

    # ── invocation (with stats tracking) ─────────────────────────────────────

    def dispatch(self, intent: str, user_input: str, **ctx: Any) -> Optional[Any]:
        """Invoke the handler for ``intent``.

        Falls back to the fallback handler if no match found.

        Returns
        -------
        Handler return value, or ``None`` if no handler is registered.
        """
        spec = self._agents.get(intent.upper())
        if spec is None:
            if self._fallback_handler:
                log.debug("No handler for intent '%s'; using fallback", intent)
                return self._fallback_handler(user_input, **ctx)
            log.warning("No handler for intent '%s' and no fallback", intent)
            return None

        try:
            spec.call_count += 1
            return spec.handler(user_input, **ctx)
        except Exception:
            spec.error_count += 1
            log.exception("Agent handler for '%s' raised an exception", intent)
            if self._fallback_handler:
                return self._fallback_handler(user_input, **ctx)
            return None

    # ── stats ────────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, dict]:
        return {
            spec.intent: {
                "calls": spec.call_count,
                "errors": spec.error_count,
                "requires_vector_db": spec.requires_vector_db,
            }
            for spec in self._agents.values()
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"AgentRegistry(intents={self.list_intents()})"


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

agent_registry = AgentRegistry()
