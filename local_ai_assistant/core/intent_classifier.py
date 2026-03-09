"""
core/intent_classifier.py
==========================
LLM-powered intent classifier with conversation-context awareness.

This is a standalone, injectable interface for intent classification.
It wraps the existing Ollama-backed classifer from
``agents.core.planner_agent`` and adds:

- Short-term context injection (last 3 turns) so follow-up queries
  like "tell me more" resolve to the correct intent.
- Memory-aware enrichment: stored user facts are passed as system context.
- Fallback chain: LLM → regex fast-path → "GENERAL".

Supported intents
-----------------
GREETING, TIME, DATE, CHAT, GENERAL,
RETRIEVAL, SUMMARY, TOPIC, DOCUMENT_LIST,
EMAIL_SUMMARY, EMAIL_SEARCH,
REMINDER_SET, REMINDER_LIST, REMINDER_DELETE,
AUDIO_TRANSCRIBE, AUDIO_QUERY, AUDIO_LIST,
COMPARE

Usage::

    from core.intent_classifier import intent_classifier

    intent = intent_classifier.classify("how many employees in 2024?")
    # → "RETRIEVAL"

    intent = intent_classifier.classify(
        "tell me more",
        history=[{"role": "user", "content": "search my emails for invoice"}]
    )
    # → "EMAIL_SEARCH"  (resolved from context)
"""

from __future__ import annotations

from typing import Optional

from core.logging_config import get_logger

log = get_logger(__name__)

_VALID_INTENTS: frozenset[str] = frozenset({
    "GREETING", "TIME", "DATE",
    "REMINDER_SET", "REMINDER_LIST", "REMINDER_DELETE",
    "EMAIL_SUMMARY", "EMAIL_SEARCH",
    "DOCUMENT_LIST", "SUMMARY", "TOPIC", "RETRIEVAL",
    "AUDIO_TRANSCRIBE", "AUDIO_QUERY", "AUDIO_LIST",
    "COMPARE", "CHAT", "GENERAL",
})

_CONTEXT_SYSTEM_PROMPT = """You are an intent classifier for a personal AI assistant.
Classify the user message into EXACTLY ONE of these intents (output only the label):

GREETING, TIME, DATE, CHAT, GENERAL,
RETRIEVAL, SUMMARY, TOPIC, DOCUMENT_LIST,
EMAIL_SUMMARY, EMAIL_SEARCH,
REMINDER_SET, REMINDER_LIST, REMINDER_DELETE,
AUDIO_TRANSCRIBE, AUDIO_QUERY, AUDIO_LIST,
COMPARE

Rules:
- Use the preceding conversation turns (if any) to resolve ambiguous follow-up queries.
- If the user said "tell me more" after an email search, return EMAIL_SEARCH.
- Factual questions about documents/company data → RETRIEVAL.
- Summarise all documents → SUMMARY.  Summarise inbox → EMAIL_SUMMARY.
- Audio/voice/recording/transcript → AUDIO_* family.
- Pure small-talk that requires no data → CHAT.
- When in doubt, prefer RETRIEVAL over GENERAL for factual questions.
- Output ONLY the intent label. No punctuation. No explanation."""


class IntentClassifier:
    """LLM-based intent classifier with context awareness."""

    def __init__(self, model_name: Optional[str] = None) -> None:
        self._model_name = model_name

    def _model(self) -> str:
        if self._model_name:
            return self._model_name
        try:
            from configs.settings import settings
            return settings.model_name
        except Exception:
            return "llama3.2:1b"

    # ── public API ─────────────────────────────────────────────────────────

    def classify(
        self,
        query: str,
        history: Optional[list[dict]] = None,
        memory_facts: Optional[dict[str, str]] = None,
    ) -> str:
        """Return the intent label for *query*.

        Parameters
        ----------
        query:
            The latest user utterance.
        history:
            Optional list of recent ``{"role": ..., "content": ...}`` turns
            (most recent last).  Up to the last 3 are used.
        memory_facts:
            Optional dict of ``{key: value}`` facts from ConversationMemory.
            Injected into the system prompt so the LLM has user context.
        """
        text = query.strip()

        # 1. Regex fast-path (instant, no LLM call for obvious patterns)
        fast = self._regex_fastpath(text)
        if fast:
            log.debug("Intent (regex): %s for %r", fast, text[:60])
            return fast

        # 2. LLM classification with context
        llm_result = self._llm_classify(text, history, memory_facts)
        if llm_result:
            log.debug("Intent (LLM): %s for %r", llm_result, text[:60])
            return llm_result

        # 3. Fallback to planner_agent regex (comprehensive)
        try:
            from agents.core.planner_agent import decide_intent
            result = decide_intent(text)
            log.debug("Intent (planner fallback): %s for %r", result, text[:60])
            return result
        except Exception as exc:
            log.warning("Planner fallback failed: %s", exc)

        return "GENERAL"

    # ── LLM backend ────────────────────────────────────────────────────────

    def _llm_classify(
        self,
        text: str,
        history: Optional[list[dict]],
        memory_facts: Optional[dict[str, str]],
    ) -> Optional[str]:
        try:
            import ollama

            system = _CONTEXT_SYSTEM_PROMPT
            if memory_facts:
                fact_lines = "\n".join(f"  {k}: {v}" for k, v in memory_facts.items())
                system += f"\n\nStored user facts:\n{fact_lines}"

            messages: list[dict] = [{"role": "system", "content": system}]

            # Include last 3 turns for context
            if history:
                messages.extend(history[-3:])

            messages.append({"role": "user", "content": text})

            resp = ollama.chat(
                model=self._model(),
                options={"temperature": 0.0, "num_predict": 10},
                messages=messages,
            )
            raw = resp.get("message", {}).get("content", "").strip().upper()
            # Take first word only
            import re
            label = re.split(r"[\s\n\r\.,\-]+", raw)[0]
            if label in _VALID_INTENTS:
                return label
        except Exception as exc:
            log.debug("LLM intent classification failed: %s", exc)
        return None

    # ── Fast-path regex ────────────────────────────────────────────────────

    @staticmethod
    def _regex_fastpath(text: str) -> Optional[str]:
        """Instant classification for completely unambiguous patterns."""
        import re
        t = text.lower().strip()

        # Greetings
        if re.match(r"^(hi+(\s+there)?|hello(\s+there)?|hey(\s+there)?|howdy|good\s*(morning|afternoon|evening|night))[\s!,.*]*$", t):
            return "GREETING"

        # Short replies → CHAT
        if re.match(r"^(yes|yeah|no|nope|ok|okay|sure|thanks|thank you|got it|cool|nice|alright|nah|yep|yup|bye)[\s!.,]*$", t):
            return "CHAT"

        # Time / date
        if re.search(r"\b(what time is it|current time|tell me the time)\b", t):
            return "TIME"
        if re.search(r"\b(what('?s| is) today'?s? date|today'?s date|current date)\b", t):
            return "DATE"

        return None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
intent_classifier = IntentClassifier()
