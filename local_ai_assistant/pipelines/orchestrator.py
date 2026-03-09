"""
pipelines/orchestrator.py
==========================
Central request orchestrator — replaces the ~300-line routing block in
``smart_agent.py``.

Design
------
- ``Orchestrator.run(user_input)`` returns an ``AgentResponse`` dataclass.
- Intent is resolved by ``core.intent_classifier.IntentClassifier``
  (LLM + regex + planner_agent fallback chain), with conversation history
  and user facts injected for context.
- ``core.router.Router`` maps the intent label to a canonical tool name.
- ``core.tool_executor.ToolExecutor`` runs the tool and returns a
  ``ToolResult``.
- ``memory.conversation_memory.ConversationMemory`` stores each turn
  and auto-extracts key facts (name, preferences, roles …).
- ``core.logger.AgentLogger`` records every request as a JSON log line.
- Vector-DB readiness is checked once and propagated as needed.
- All exceptions are caught and converted to error responses — the CLI
  never crashes due to an unhandled exception in a handler.

Usage::

    from pipelines.orchestrator import orchestrator

    response = orchestrator.run("how many employees in 2024?")
    print(response.answer)
    if response.source:
        print("Source:", response.source)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from core.logging_config import get_logger
from configs.settings import settings

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lazy singletons — imported inside methods to avoid circular imports and
# to allow each module to initialise on first use.
# ---------------------------------------------------------------------------

def _get_memory():
    try:
        from memory.conversation_memory import conversation_memory
        return conversation_memory
    except Exception as exc:
        log.debug("ConversationMemory unavailable: %s", exc)
        return None


def _get_intent_classifier():
    try:
        from core.intent_classifier import intent_classifier
        return intent_classifier
    except Exception as exc:
        log.debug("IntentClassifier unavailable: %s", exc)
        return None


def _get_router():
    try:
        from core.router import router
        return router
    except Exception as exc:
        log.debug("Router unavailable: %s", exc)
        return None


def _get_tool_executor():
    try:
        from core.tool_executor import tool_executor
        return tool_executor
    except Exception as exc:
        log.debug("ToolExecutor unavailable: %s", exc)
        return None


def _get_agent_logger():
    try:
        from core.logger import agent_logger
        return agent_logger
    except Exception as exc:
        log.debug("AgentLogger unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# AgentResponse
# ---------------------------------------------------------------------------

@dataclass
class AgentResponse:
    """Structured return value from every orchestrator dispatch."""

    answer: str
    intent: str = "UNKNOWN"
    source: Optional[str] = None
    latency_ms: float = 0.0
    bullets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Routes user requests to the correct agent handler."""

    # Short one-word conversational inputs never need doc lookup
    _CONVERSATIONAL: frozenset[str] = frozenset({
        "yes", "no", "ok", "okay", "sure", "thanks",
        "thank you", "alright", "nope", "yep", "yup",
        "nah", "bye", "got it", "cool", "nice",
    })

    def __init__(self) -> None:
        self._last_email_fetch: float = 0.0

    # ── main entry point ────────────────────────────────────────────────────

    def run(self, user_input: str) -> AgentResponse:  # noqa: C901
        """Process *user_input* and return a structured ``AgentResponse``."""
        t0 = time.perf_counter()

        text = user_input.strip()
        if not text:
            return AgentResponse(answer="", intent="EMPTY")

        # ── 1. Conversational shortcircuit ──────────────────────────────────
        if text.lower() in self._CONVERSATIONAL:
            resp = self._handle_chat(text)
            resp.intent = "CHAT"
            resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, resp)
            return resp

        # ── 2. System quick-check (date/time — no LLM needed) ────────────────
        sys_resp = self._handle_system(text)
        if sys_resp is not None:
            sys_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, sys_resp)
            return sys_resp

        # ── 3. Auto-extract user facts from input ───────────────────────────
        memory = _get_memory()
        if memory is not None:
            try:
                memory.extract_and_store(text)
            except Exception as exc:
                log.debug("memory.extract_and_store failed: %s", exc)

        # ── 4. Intent classification ─────────────────────────────────────────
        intent = self._classify_intent(text, memory)
        log.debug("Intent: %s  |  input: %.60s", intent, text)

        # ── 5. Route intent → tool_name ─────────────────────────────────────
        router = _get_router()
        tool_name: Optional[str] = None
        if router is not None:
            try:
                tool_name = router.route(intent)
            except Exception as exc:
                log.debug("Router.route failed: %s", exc)

        # ── 6. Execute tool (if any) ─────────────────────────────────────────
        tool_result = None
        if tool_name is not None:
            tool_executor = _get_tool_executor()
            if tool_executor is not None:
                try:
                    tool_result = tool_executor.execute(tool_name, text)
                except Exception as exc:
                    log.exception("ToolExecutor.execute(%r) raised: %s", tool_name, exc)

        # ── 7. Build response ────────────────────────────────────────────────
        try:
            if tool_result is not None and tool_result.success:
                # Tool returned a valid answer — wrap it
                bullets = _to_bullets(tool_result.output) if tool_result.output else []
                resp = AgentResponse(
                    answer=tool_result.output or "",
                    intent=intent,
                    source=tool_result.source,
                    bullets=bullets,
                )
            else:
                # No tool hit or tool failed — fall through to legacy dispatch
                resp = self._dispatch(intent, text, memory=memory)
        except Exception as exc:
            log.exception("Handler for '%s' raised: %s", intent, exc)
            resp = AgentResponse(
                answer="Something went wrong. Please try again.",
                intent=intent,
            )

        resp.intent = intent
        resp.latency_ms = (time.perf_counter() - t0) * 1_000

        # ── 8. Log + update memory ───────────────────────────────────────────
        self._post_process(text, resp, tool_name=tool_name, tool_result=tool_result)
        return resp

    # ── intent classification (new path + legacy fallback) ──────────────────

    def _classify_intent(self, text: str, memory) -> str:
        """Try IntentClassifier first; fall back to planner_agent."""
        # Build history list for context (last 6 turns)
        history: list[str] = []
        facts: dict = {}
        if memory is not None:
            try:
                history = [
                    f"{t['role']}: {t['content']}"
                    for t in memory.get_history(last_n=6)
                ]
                facts = memory.list_facts()
            except Exception:
                pass

        clf = _get_intent_classifier()
        if clf is not None:
            try:
                return clf.classify(text, history=history, memory_facts=facts)
            except Exception as exc:
                log.debug("IntentClassifier.classify failed: %s — falling back", exc)

        # Legacy path
        try:
            from agents.core.planner_agent import decide_intent
            return decide_intent(text)
        except Exception as exc:
            log.exception("decide_intent failed: %s", exc)
            return "GENERAL"

    # ── post-processing: memory update + structured log ──────────────────────

    def _post_process(
        self,
        user_input: str,
        resp: AgentResponse,
        tool_name: Optional[str] = None,
        tool_result=None,
    ) -> None:
        """Store conversation turn and emit structured log."""
        memory = _get_memory()
        if memory is not None:
            try:
                memory.add_turn("user", user_input)
                if resp.answer:
                    memory.add_turn("assistant", resp.answer[:500])
            except Exception as exc:
                log.debug("memory.add_turn failed: %s", exc)

        agent_logger = _get_agent_logger()
        if agent_logger is not None:
            try:
                agent_logger.log_request(
                    query=user_input,
                    intent=resp.intent,
                    tool=tool_name,
                    result=resp.answer[:200] if resp.answer else None,
                    latency_ms=resp.latency_ms,
                    source=resp.source,
                    error=(tool_result.error if tool_result and not tool_result.success else None),
                )
            except Exception as exc:
                log.debug("agent_logger.log_request failed: %s", exc)

    # ── dispatcher ──────────────────────────────────────────────────────────

    def _dispatch(self, intent: str, text: str, memory=None) -> AgentResponse:  # noqa: C901
        match intent:
            case "CHAT":
                return self._handle_chat(text, memory=memory)
            case "GENERAL":
                return self._handle_general(text, memory=memory)
            case "TIME":
                return AgentResponse(
                    answer=datetime.now().strftime("%H:%M:%S"),
                    intent="TIME",
                )
            case "DATE":
                return AgentResponse(
                    answer=datetime.now().strftime("%A, %d %B %Y"),
                    intent="DATE",
                )
            case "GREETING":
                return AgentResponse(
                    answer="Hello! How can I help you today?",
                    intent="GREETING",
                )
            case "REMINDER_SET" | "REMINDERS_SET":
                return self._handle_reminder_set(text)
            case "REMINDER_LIST" | "REMINDERS_LIST":
                return self._handle_reminder_list()
            case "REMINDER_DELETE" | "REMINDERS_DELETE":
                return self._handle_reminder_delete(text)
            case "EMAIL_SUMMARY" | "EMAIL_SUMMARIZE":
                return self._handle_email_summary()
            case "EMAIL_SEARCH" | "EMAIL_QUERY" | "EMAIL":
                return self._handle_email_search(text)
            case "DOCUMENT_LIST":
                return self._handle_document_list()
            case "RETRIEVAL" | "DOCUMENT_SEARCH":
                return self._handle_retrieval(text)
            case "SUMMARY" | "DOCUMENT_SUMMARY":
                return self._handle_summary()
            case "TOPIC" | "TOPICS" | "DOCUMENT_TOPICS":
                return self._handle_topic()
            case "COMPARE" | "COMPARISON" | "SYSTEM_COMPARE":
                return self._handle_compare(text)
            case _:
                return self._handle_chat(text, memory=memory)

    # ── system date/time (runs before LLM) ──────────────────────────────────

    def _handle_system(self, text: str) -> Optional[AgentResponse]:
        t = text.lower()
        if "today" in t and "date" in t:
            return AgentResponse(
                answer=f"Today's date is {datetime.now().strftime('%A, %d %B %Y')}",
                intent="DATE",
            )
        if "tomorrow" in t:
            nxt = datetime.now() + timedelta(days=1)
            return AgentResponse(
                answer=f"Tomorrow's date is {nxt.strftime('%A, %d %B %Y')}",
                intent="DATE",
            )
        return None

    # ── CHAT / GENERAL ───────────────────────────────────────────────────────

    def _handle_chat(self, text: str, memory=None) -> AgentResponse:
        from agents.core.general_agent import handle_general

        # Build memory-enriched system prompt
        system_extra = ""
        if memory is not None:
            try:
                summary = memory.facts_summary()
                if summary:
                    system_extra = f"\n\n{summary}"
            except Exception:
                pass

        answer = handle_general(text, settings.model_name, system_extra=system_extra)
        return AgentResponse(answer=answer or "")

    def _handle_general(self, text: str, memory=None) -> AgentResponse:
        """Try doc retrieval first; fall back to LLM with memory context."""
        from agents.core.general_agent import handle_general

        db = self._get_vector_db()
        if db is not None:
            try:
                from agents.knowledge.retrieval_agent import handle_retrieval
                ans, src = handle_retrieval(
                    text, db, settings.retrieval_threshold, settings.model_name
                )
                if ans:
                    q_tokens = [
                        t for t in text.lower().split()
                        if len(t) > 3
                        and t not in {
                            "what", "this", "that", "with", "have", "from",
                            "will", "your", "more", "about", "tell", "give",
                            "make", "show", "does", "which", "when", "where",
                            "how", "are", "you", "the", "and", "not", "but",
                            "can", "all", "its", "was", "had", "has", "did",
                        }
                    ]
                    if q_tokens and any(tok in ans.lower() for tok in q_tokens):
                        bullets = _to_bullets(ans)
                        return AgentResponse(
                            answer=ans,
                            source=src,
                            bullets=bullets,
                        )
            except Exception as exc:
                log.debug("Retrieval failed in GENERAL handler: %s", exc)

        # Memory-enriched LLM fallback
        system_extra = ""
        if memory is not None:
            try:
                summary = memory.facts_summary()
                if summary:
                    system_extra = f"\n\n{summary}"
            except Exception:
                pass

        answer = handle_general(text, settings.model_name, system_extra=system_extra)
        return AgentResponse(answer=answer or "")

    # ── REMINDERS ────────────────────────────────────────────────────────────

    def _handle_reminder_set(self, text: str) -> AgentResponse:
        from agents.tasks.reminder_agent import (
            extract_reminder_details,
            add_reminder,
        )
        rtext, rtime = extract_reminder_details(text)
        if not rtime:
            return AgentResponse(
                answer=(
                    "I could not understand the reminder time. "
                    "Try 'remind me at 15:22' or 'remind me in 10 minutes'."
                ),
            )
        # Return parsed details; the CLI confirms before saving
        return AgentResponse(
            answer=f"__CONFIRM_REMINDER__{rtext}||{rtime}",
        )

    def _handle_reminder_list(self) -> AgentResponse:
        from agents.tasks.reminder_agent import list_reminders
        return AgentResponse(answer=list_reminders() or "No reminders set.")

    def _handle_reminder_delete(self, text: str) -> AgentResponse:
        # Can't prompt inline; return a sentinel that main.py handles
        return AgentResponse(answer="__PROMPT_REMINDER_DELETE__")

    # ── EMAILS ───────────────────────────────────────────────────────────────

    def _handle_email_summary(self) -> AgentResponse:
        self._auto_fetch_emails(force=True)
        from agents.knowledge.email_summarizer_agent import handle_email_summary
        return AgentResponse(answer=handle_email_summary() or "No emails to summarise.")

    def _handle_email_search(self, text: str) -> AgentResponse:
        self._auto_fetch_emails(force=True)
        try:
            from agents.knowledge.email_summarizer_agent import summarize_emails_by_query
            answer = summarize_emails_by_query(text, max_results=8)
        except Exception:
            from agents.knowledge.email_query_agent import search_emails_by_text
            results = search_emails_by_text(text)
            lines = [
                f"- {r.get('id')} | {r.get('subject','(no subject)')} from {r.get('from','?')}"
                for r in results
            ]
            answer = "\n".join(lines) if lines else "No matching emails found."
        return AgentResponse(answer=answer)

    def _auto_fetch_emails(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_email_fetch) < settings.email_fetch_cooldown:
            return
        try:
            from agents.tasks.email_agent import EmailAgent
            from agents.knowledge.email_query_agent import invalidate_email_cache
            invalidate_email_cache()
            agent = EmailAgent()
            if hasattr(agent, "fetch_recent_emails"):
                new_emails = agent.fetch_recent_emails(last_n=settings.email_fetch_count)
            else:
                new_emails = agent.fetch_unread_emails()
            if new_emails:
                agent.save_to_cache(new_emails)
            self._last_email_fetch = now
        except Exception as exc:
            log.debug("Email IMAP fetch failed: %s", exc)
            self._last_email_fetch = now  # back off

    # ── DOCUMENTS ────────────────────────────────────────────────────────────

    def _handle_document_list(self) -> AgentResponse:
        from agents.knowledge.document_list_agent import list_all_documents
        return AgentResponse(answer=list_all_documents() or "No documents found.")

    def _handle_retrieval(self, text: str) -> AgentResponse:
        from agents.knowledge.retrieval_agent import handle_retrieval
        db = self._get_vector_db()
        if db is None:
            from agents.core.general_agent import handle_general
            answer = handle_general(text, settings.model_name)
            return AgentResponse(
                answer=answer or "Knowledge base is still loading. Please try again shortly."
            )
        answer, source = handle_retrieval(
            text, db, settings.retrieval_threshold, settings.model_name
        )
        bullets = _to_bullets(answer) if answer else []
        return AgentResponse(
            answer=answer or "",
            source=source,
            bullets=bullets,
        )

    def _handle_summary(self) -> AgentResponse:
        from agents.knowledge.summary_agent import handle_summary
        from services.document_service import document_service
        docs = document_service.get_documents()
        summary = handle_summary(docs, settings.model_name)
        bullets = _to_bullets(summary, max_bullets=8) if summary else []
        return AgentResponse(answer=summary or "", bullets=bullets)

    def _handle_topic(self) -> AgentResponse:
        from agents.knowledge.topic_agent import handle_topics
        from services.document_service import document_service
        docs = document_service.get_documents()
        answer = handle_topics(docs, settings.model_name)
        return AgentResponse(answer=answer or "")

    # ── COMPARE ──────────────────────────────────────────────────────────────

    def _handle_compare(self, text: str) -> AgentResponse:
        from agents.core.general_agent import handle_general
        from agents.knowledge.retrieval_agent import handle_retrieval

        a, b = _parse_comparison(text)

        facts: list[str] = []
        sources: list[str] = []

        db = self._get_vector_db()
        for item in (a, b):
            if not item:
                continue
            try:
                ans, src = handle_retrieval(
                    item, db, settings.retrieval_threshold, settings.model_name
                )
                if ans:
                    facts.append(f"Facts about {item}: {ans}")
                if src:
                    sources.append(f"{item}: {src}")
            except Exception:
                pass

        prompt_lines = [
            "You are an assistant that provides concise, well-structured comparisons "
            "using only the provided context facts. Do not invent facts.",
            f"User question: {text}",
            "",
        ]
        if facts:
            prompt_lines.append("Context facts (from local documents):")
            prompt_lines.extend(f"- {f}" for f in facts)
            prompt_lines.append("")
        if sources:
            prompt_lines.append("Sources:")
            prompt_lines.extend(f"- {s}" for s in sources)
            prompt_lines.append("")
        prompt_lines.extend([
            "Required format (use Markdown):",
            "# Short answer (1 sentence)",
            "## Pros\n- (bullet points)",
            "## Cons\n- (bullet points)",
            "**Recommendation:** (1 sentence)",
            "\nReturn only the Markdown-formatted comparison.",
        ])

        answer = handle_general("\n".join(prompt_lines), settings.model_name, temperature=0.0)
        return AgentResponse(answer=answer or "")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get_vector_db(self):
        try:
            from services.vector_store_service import vector_store_service
            if vector_store_service.is_ready:
                return vector_store_service.get_vector_db()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Pure helper functions (no I/O)
# ---------------------------------------------------------------------------

def _to_bullets(text: str, max_bullets: int = 6) -> list[str]:
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"[\n\r]+|(?<=[.\?!])\s+", text) if p.strip()]
    bullets: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if len(bullets) >= max_bullets:
            break
        key = p.lower()
        if key not in seen:
            seen.add(key)
            bullets.append(p)
    return bullets


def _parse_comparison(text: str) -> tuple[Optional[str], Optional[str]]:
    t = text.strip()

    def _clean(s: str) -> str:
        s = re.sub(r'^["\'\(\[\{]+', "", s)
        s = re.sub(r'["\'\)\]\},.\?\!]+$', "", s)
        return s.strip()

    # Quoted pairs: "A" vs "B"
    m = re.search(r"""[\"']([^\"']+)[\"']\s*(?:vs\.?|versus|or|,)\s*[\"']([^\"']+)[\"']""", t, re.I)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))

    # compare X and/with/to Y
    m = re.search(r"compare\s+(.+?)\s+(?:and|with|to)\s+(.+)$", t, re.I)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))

    # X vs Y
    m = re.search(r"(.+?)\s+vs\.?\s+(.+?)$", t, re.I)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))

    # X versus Y
    m = re.search(r"(.+?)\s+versus\s+(.+?)$", t, re.I)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))

    # Which is better X or Y
    m = re.search(r"which is better[:,]?\s*(.+?)\s+or\s+(.+?)\??$", t, re.I)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))

    # Simple X or Y
    m = re.search(
        r"(?:\b|\s)([\w\-\.#\+]+(?:[\s\w\-\.#\+]+)?)\s+or\s+([\w\-\.#\+]+(?:[\s\w\-\.#\+]+)?)\??$",
        t,
        re.I,
    )
    if m:
        return _clean(m.group(1)), _clean(m.group(2))

    return None, None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

orchestrator = Orchestrator()
