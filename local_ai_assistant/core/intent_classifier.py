"""
core/intent_classifier.py
==========================
Hybrid intent classifier: deterministic guardrails + LLM fallback.

Pipeline (in order, each stage short-circuits on match):
  1. Regex fast-path       — instant, no LLM, handles obvious patterns
  2. Context guardrail     — email flow state machine (search→reply→send)
  3. LLM classification    — strict JSON-only; free text → rejected, not guessed
  4. Heuristic fallback    — conservative keyword-based safety net
  5. planner_agent         — legacy regex fallback
  6. GENERAL               — final default

Key design choices:
  - LLM output that is NOT valid JSON is REJECTED (not keyword-scanned).
    Keyword scanning of free text was the root cause of misclassifications.
  - Context guardrails handle email flow without ANY LLM call.
  - format="json" is passed to Ollama to enforce structured output.
  - All log messages use ASCII only to prevent cp1252 crashes on Windows.

Usage::

    from core.intent_classifier import intent_classifier

    intent = intent_classifier.classify("how many employees in 2024?")
    # -> "RETRIEVAL"

    intent = intent_classifier.classify(
        "reply to above mail",
        history=[{"role": "assistant", "content": "Found 2 emails..."}],
        last_intent="EMAIL_SEARCH",
    )
    # -> "EMAIL_REPLY"
"""

from __future__ import annotations

import re
from typing import Optional

from core.logging_config import get_logger

log = get_logger(__name__)

_VALID_INTENTS: frozenset[str] = frozenset({
    "GREETING", "TIME", "DATE",
    "REMINDER_SET", "REMINDER_LIST", "REMINDER_DELETE",
    "EMAIL_SUMMARY", "EMAIL_SEARCH", "EMAIL_REPLY", "EMAIL_SEND",
    "DOCUMENT_LIST", "SUMMARY", "TOPIC", "RETRIEVAL",
    "AUDIO_TRANSCRIBE", "AUDIO_QUERY", "AUDIO_LIST",
    "COMPARE", "CHAT", "GENERAL",
})

_SYSTEM_PROMPT = """You classify user messages into intent labels. Respond ONLY with JSON.

Format (required):
{"intent": "INTENT_NAME", "confidence": 0.95}

Valid intent names (pick exactly one):
GREETING, TIME, DATE, CHAT, GENERAL,
RETRIEVAL, SUMMARY, TOPIC, DOCUMENT_LIST,
EMAIL_SUMMARY, EMAIL_SEARCH, EMAIL_REPLY, EMAIL_SEND,
REMINDER_SET, REMINDER_LIST, REMINDER_DELETE,
AUDIO_TRANSCRIBE, AUDIO_QUERY, AUDIO_LIST,
COMPARE

Key classification rules:
- EMAIL_REPLY  : reply / respond / draft a reply / give response / write back / compose reply
- EMAIL_SEND   : send it / go ahead / yes send / OK send / send the reply / send the email
- EMAIL_SEARCH : find emails / search inbox / show emails from [sender] / check my emails
- REMINDER_SET : remind me at / set a reminder / alert me when
- RETRIEVAL    : questions about documents, reports, or stored data
- GENERAL      : open questions, comparisons, general knowledge

Output ONLY the JSON object. No explanations, no extra text."""


# Anaphoric / contextual reference signals that indicate a follow-up
# about the previously discussed document or image.
_FOLLOWUP_PATTERN = re.compile(
    r"\b("
    r"this|that|it|its|the same|above|previous|last|the image|the file|the document"
    r"|the pdf|the screenshot|the picture|the report"
    r"|what (does|did|is|was) it|tell me more|more (about|details?|info)"
    r"|what else|anything else|what does it (say|mean|contain|explain|show)"
    r")",
    re.IGNORECASE,
)


class IntentClassifier:
    """Hybrid intent classifier: deterministic regex + context guardrails + LLM fallback."""

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
        last_intent: Optional[str] = None,
        last_file: Optional[str] = None,
    ) -> str:
        """Classify *query* and return an intent label.

        Pipeline:
          1. Regex fast-path    — deterministic, instant
          2. Context guardrail  — email state machine (no LLM)
          3. LLM                — strict JSON only; free text = rejected
          4. Heuristic fallback — conservative keyword signals
          5. Planner agent      — legacy regex
          6. GENERAL            — final default
        """
        text = query.strip()
        if not text:
            return "GENERAL"

        # 0. Anaphoric document follow-up short-circuit (RETRIEVAL)
        if last_file and last_intent in {"RETRIEVAL", "SUMMARY", "GENERAL"} \
                and _FOLLOWUP_PATTERN.search(text):
            log.info("[INTENT] context-followup -> RETRIEVAL (last_file=%r)", last_file)
            return "RETRIEVAL"

        # 0.5. Email context override — memory-aware, fires before regex/LLM.
        # When an email is stored in memory and the input carries any reply signal
        # (including loose phrasing and typos), immediately return EMAIL_REPLY.
        email_ctx = self._email_context_override(text, last_intent)
        if email_ctx:
            log.info("[INTENT] email-context-override -> %s | %r", email_ctx, text[:60])
            return email_ctx

        # 1. Regex fast-path
        fast = self._regex_fastpath(text)
        if fast:
            log.info("[INTENT] regex -> %s | %r", fast, text[:60])
            return fast

        # 2. Context guardrail (email flow) — no LLM required
        guardrail = self._context_guardrail(text, history, last_intent)
        if guardrail:
            log.info("[INTENT] guardrail -> %s | %r", guardrail, text[:60])
            return guardrail

        # 3. LLM classification — strict JSON only
        llm_result = self._llm_classify(text, history, memory_facts)
        if llm_result and self._sanity_check(llm_result, text):
            log.info("[INTENT] LLM -> %s | %r", llm_result, text[:60])
            return llm_result
        if llm_result:
            log.warning(
                "[INTENT] LLM result %r rejected by sanity check for %r",
                llm_result, text[:60],
            )

        # 4. Heuristic fallback (when LLM fails or is unavailable)
        heuristic = self._heuristic_fallback(text, last_intent)
        if heuristic:
            log.info("[INTENT] heuristic -> %s | %r (LLM failed)", heuristic, text[:60])
            return heuristic

        # 5. Legacy planner-agent regex
        try:
            from agents.core.planner_agent import decide_intent
            result = decide_intent(text)
            log.info("[INTENT] planner -> %s | %r", result, text[:60])
            return result
        except Exception as exc:
            log.warning("[INTENT] planner fallback failed: %s", exc)

        log.info("[INTENT] default -> GENERAL | %r", text[:60])
        return "GENERAL"

    # ── email context override (memory-aware) ─────────────────────────────

    # Broad reply-signal pattern — intentionally loose to catch typos and
    # paraphrases.  Used only when email context already exists in memory.
    _BROAD_REPLY_RE = re.compile(
        r"\b("
        r"rep(?:ly|lies|lied|lying|ond|onds|onded|onding|onse|onses"
        r"|pond|ponse|ponding|ly|ly\b)"
        r"|respond|response|reply"
        r"|write\s+back"
        r"|give\s+.{0,20}(reply|response|answer|repl\w*|resp\w*)"
        r"|draft\s+.{0,15}(reply|response)"
        r"|compose\s+.{0,15}(reply|response)"
        r"|tell\s+(him|her|them)"
        r"|above\s+(mail|email|message)"
        r"|reply\s+to|respond\s+to"
        r")\b",
        re.IGNORECASE,
    )

    def _email_context_override(
        self,
        text: str,
        last_intent: Optional[str],
    ) -> Optional[str]:
        """Return EMAIL_REPLY when email context exists in memory and text implies a response.

        This is intentionally broad — it fires on typos, indirect phrasing, and
        vague references ("give response", "repond to above", "tell her") whenever
        a concrete email is already in session memory.  It runs BEFORE the regex
        fast-path so it cannot be overridden by a wrong regex classification.
        """
        if not self._BROAD_REPLY_RE.search(text):
            return None

        try:
            from memory.conversation_memory import conversation_memory

            last_email = conversation_memory.get_last_email()
            if last_email:
                log.info(
                    "[INTENT] email-context-override: last_email present "
                    "(from=%s), broad reply signal matched -> EMAIL_REPLY",
                    str(last_email.get("from", "?"))[:40],
                )
                return "EMAIL_REPLY"

            # Also fire when search results are present (user searched, now replying)
            search_results = conversation_memory.get_last_email_search_results()
            if search_results:
                log.info(
                    "[INTENT] email-context-override: %d search results in memory, "
                    "broad reply signal matched -> EMAIL_REPLY",
                    len(search_results),
                )
                return "EMAIL_REPLY"

            log.info(
                "[INTENT] email-context-override: broad reply signal present BUT "
                "no email in memory — letting normal pipeline decide. input=%r",
                text[:60],
            )
        except Exception as exc:
            log.debug("[INTENT] email-context-override memory check failed: %s", exc)

        return None

    # ── context guardrail ───────────────────────────────────────────────────

    def _context_guardrail(
        self,
        text: str,
        history: Optional[list[dict]],
        last_intent: Optional[str],
    ) -> Optional[str]:
        """Deterministic email flow transitions — no LLM required.

        Handles:
          EMAIL_SEARCH -> EMAIL_REPLY  (user asks to reply after a search)
          EMAIL_REPLY  -> EMAIL_SEND   (user confirms sending the draft)
        Also reads history to detect shown drafts and search results.
        """
        t = text.lower().strip()

        _REPLY_SIG = re.compile(
            r"\b(reply|respond|response|draft|compose|answer|write\s*back"
            r"|give.{0,12}(reply|response|respond)"
            r"|rep(?:ond|onse|pond|ponse)"          # typo variants"
            r"|tell\s+(him|her|them)"
            r"|above\s+(mail|email|message)"
            r"|reply\s+to|respond\s+to)\b",
            re.IGNORECASE,
        )
        _SEND_CONFIRM = re.compile(
            r"^(yes|ok|sure|send|go|proceed|confirm|send\s+it|go\s+ahead"
            r"|yes\s+please|yes\s+send|ok\s+send)[\s!.,]*$",
            re.IGNORECASE,
        )
        _SEND_SIG = re.compile(
            r"\b(send\s+it|send\s+the|go\s+ahead|yes\s+send|ok\s+send)\b",
            re.IGNORECASE,
        )

        # After email search/reply: any reply signal -> EMAIL_REPLY
        if last_intent in {"EMAIL_SEARCH", "EMAIL_REPLY"}:
            if _REPLY_SIG.search(t) and not _SEND_SIG.search(t):
                log.info(
                    "[INTENT] guardrail: last_intent=%s + reply signal -> EMAIL_REPLY",
                    last_intent,
                )
                return "EMAIL_REPLY"

        # After email reply draft: confirmation -> EMAIL_SEND
        if last_intent == "EMAIL_REPLY":
            if _SEND_CONFIRM.search(t) or _SEND_SIG.search(t):
                log.info("[INTENT] guardrail: EMAIL_REPLY + send confirm -> EMAIL_SEND")
                return "EMAIL_SEND"

        # Scan conversation history for shown drafts / search results
        if history:
            has_draft = False
            has_search_result = False
            for turn in reversed(history[-6:]):
                if not isinstance(turn, dict):
                    continue
                role = turn.get("role", "")
                content = (turn.get("content") or "").lower()
                if role == "assistant":
                    if "draft reply" in content or "draft created" in content \
                            or "draft id:" in content:
                        has_draft = True
                        break
                    if ("found" in content or "email(s)" in content) \
                            and ("email" in content or "mail" in content):
                        has_search_result = True

            if has_draft:
                # After a draft was shown, confirmation -> EMAIL_SEND
                if _SEND_CONFIRM.search(t) or _SEND_SIG.search(t):
                    log.info(
                        "[INTENT] guardrail: draft in history + send confirm -> EMAIL_SEND"
                    )
                    return "EMAIL_SEND"
                # Re-draft request
                if _REPLY_SIG.search(t):
                    log.info(
                        "[INTENT] guardrail: draft in history + reply signal -> EMAIL_REPLY"
                    )
                    return "EMAIL_REPLY"

            if has_search_result and not has_draft:
                if _REPLY_SIG.search(t) and not _SEND_SIG.search(t):
                    log.info(
                        "[INTENT] guardrail: search result in history + reply signal"
                        " -> EMAIL_REPLY"
                    )
                    return "EMAIL_REPLY"

        return None

    # ── LLM backend ────────────────────────────────────────────────────────

    def _llm_classify(
        self,
        text: str,
        history: Optional[list[dict]],
        memory_facts: Optional[dict[str, str]],
    ) -> Optional[str]:
        """Classify via LLM. Returns None if response is not valid JSON.

        Does NOT fall back to keyword scanning — that causes misclassifications.
        """
        try:
            import json as _json
            import ollama

            system = _SYSTEM_PROMPT
            if memory_facts:
                fact_lines = "\n".join(f"  {k}: {v}" for k, v in memory_facts.items())
                system += f"\n\nUser context:\n{fact_lines}"

            messages: list[dict] = [{"role": "system", "content": system}]
            if history:
                messages.extend(history[-3:])
            messages.append({"role": "user", "content": text})

            log.info(
                "[INTENT_CLASSIFIER] LLM call: %d history turns, query=%r",
                len(history or []), text[:60],
            )

            resp = ollama.chat(
                model=self._model(),
                messages=messages,
                format="json",                      # Force structured JSON output
                options={"temperature": 0.0, "num_predict": 80},
            )
            raw = (resp.get("message", {}).get("content") or "").strip()
            log.info("[INTENT_CLASSIFIER] LLM raw: %s", raw[:150])

            result = self._parse_json_intent(raw)
            if result:
                log.info("[INTENT_CLASSIFIER] parsed -> %s", result)
            else:
                log.warning(
                    "[INTENT_CLASSIFIER] REJECTED non-JSON response: %s", raw[:100]
                )
            return result

        except Exception as exc:
            log.warning("[INTENT_CLASSIFIER] LLM call failed: %s", exc)
        return None

    # ── JSON parser ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json_intent(raw: str) -> Optional[str]:
        """Parse strict JSON intent from LLM response.

        Handles:
          - Plain JSON:       {"intent": "EMAIL_SEARCH", "confidence": 0.9}
          - Markdown fences:  ```json\\n{"intent": ...}\\n```
          - Inline JSON:      some text {"intent": "..."} trailing text

        Rejects free text entirely — no keyword scanning.
        Returns None on any failure.
        """
        import json as _json

        if not raw:
            return None

        # Step 1: strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()

        # Step 2: extract innermost { ... "intent": "..." ... } object
        json_match = re.search(
            r'\{[^{}]*"intent"\s*:\s*"([^"]+)"[^{}]*\}',
            cleaned,
            re.DOTALL,
        )
        if json_match:
            try:
                data = _json.loads(json_match.group(0))
                intent = data.get("intent", "").strip().upper()
                if intent in _VALID_INTENTS:
                    log.info(
                        "[INTENT_CLASSIFIER] JSON ok: intent=%s confidence=%.2f",
                        intent, data.get("confidence", 0.0),
                    )
                    return intent
                log.warning("[INTENT_CLASSIFIER] invalid intent value: %r", intent)
                return None
            except _json.JSONDecodeError:
                pass

        # Step 3: try parsing the entire cleaned string
        try:
            data = _json.loads(cleaned)
            if isinstance(data, dict) and "intent" in data:
                intent = str(data["intent"]).strip().upper()
                if intent in _VALID_INTENTS:
                    return intent
                log.warning("[INTENT_CLASSIFIER] invalid intent value: %r", intent)
                return None
        except _json.JSONDecodeError:
            pass

        # Step 4: REJECT — do NOT scan free text (unreliable, causes wrong classifications)
        log.warning(
            "[INTENT_CLASSIFIER] not valid JSON, rejecting. raw=%r", raw[:120]
        )
        return None

    # ── sanity check ────────────────────────────────────────────────────────

    @staticmethod
    def _sanity_check(intent: str, text: str) -> bool:
        """Return False when the LLM result is obviously wrong.

        The small model (llama3.2:1b) sometimes returns GREETING or CHAT
        for arbitrary questions.  Reject those cases so the heuristic
        fallback can produce a better answer.
        """
        t = text.lower().strip()

        # GREETING must look like a greeting (short, no question words)
        if intent == "GREETING":
            if len(t) > 30:
                return False
            if re.search(r"\b(what|who|when|where|why|how|which|compare|vs\.?|better|worse)\b", t):
                return False

        # CHAT must be short social chitchat
        if intent == "CHAT" and len(t) > 40:
            if re.search(r"\b(email|remind|send|file|document|audio|search|find)\b", t):
                return False

        return True

    # ── heuristic fallback ──────────────────────────────────────────────────

    @staticmethod
    def _heuristic_fallback(text: str, last_intent: Optional[str] = None) -> Optional[str]:
        """Conservative keyword-based fallback when LLM fails.

        Only fires on high-confidence unambiguous signals to avoid false positives.
        """
        t = text.lower().strip()

        # Email reply signal — only when no document-domain keywords are present
        if re.search(r"\b(reply|respond|response)\b", t):
            if not re.search(
                r"\b(files?|documents?|docs?|folder|directory"
                r"|summarize|summarise|list|show\s+files?)\b",
                t,
            ):
                return "EMAIL_REPLY"

        # Reminder
        if re.search(r"\b(remind|reminder)\b", t):
            return "REMINDER_SET"

        # Email search
        if re.search(
            r"\b(find\s+email|search\s+(my\s+)?(email|inbox)|check\s+(my\s+)?email)\b", t
        ):
            return "EMAIL_SEARCH"

        # Email context: give/write/draft + email context
        if last_intent in {"EMAIL_SEARCH", "EMAIL_REPLY"}:
            if re.search(r"\b(give|write|draft|compose|make|create)\b", t):
                return "EMAIL_REPLY"

        # General question shape
        if re.search(
            r"\b(what|who|when|where|why|how|which|is|are|was|were)\b", t
        ) and len(t) > 15:
            return "GENERAL"

        return None

    # ── Fast-path regex ────────────────────────────────────────────────────

    @staticmethod
    def _regex_fastpath(text: str) -> Optional[str]:
        """Instant deterministic classification for unambiguous patterns.

        Runs before any LLM call. Returns None if no pattern matches
        (caller proceeds to the next pipeline stage).
        """
        import re as _re

        t = text.lower().strip()

        # Greetings
        if _re.match(
            r"^(hi+(\s+there)?|hello(\s+there)?|hey(\s+there)?|howdy"
            r"|good\s*(morning|afternoon|evening|night))[\s!,.*]*$",
            t,
        ):
            return "GREETING"

        # =====================================================================
        # EMAIL INTENTS  (check before generic patterns to avoid misclassification)
        # =====================================================================
        _EMAIL_W = r"(?:mail(?:s|box)?|email(?:s|box)?|inbox|messages?)"

        # EMAIL_SEND — check before CHAT so short confirmations don't become CHAT
        send_patterns = [
            # Explicit send + object
            r"\b(send|submit|dispatch|transmit|deliver)\b.{0,40}"
            r"\b(the\s+)?(email|reply|message|response|draft|it)\b",
            r"\b(send|submit|go|proceed)\s+(the\s+)?(reply|email|message|response|it)\b",
            r"\b(send|submit)\s+(the\s+)?(reply|email|message|draft)\b",
            r"\b(send\s+it|send\s+the\s+reply|send\s+the\s+email|send\s+away|ok\s+go"
            r"|do\s+it)\b",
            r"\b(go\s+ahead|let'?s\s+proceed|send\s+away)\b.{0,30}"
            r"\b(with\s+)?(the\s+)?(email|reply|message|response)\b",
            # "yes send it", "yes please send"
            r"^(yes|ok|sure)\s+(send|please\s+send|go\s+ahead)[\s!.,\w]*$",
            # Standalone short confirmations (only EMAIL_SEND, not CHAT)
            r"^(go\s+ahead|proceed|do\s+it)[\s!.,]*$",
        ]
        for pat in send_patterns:
            if _re.search(pat, t, _re.IGNORECASE):
                return "EMAIL_SEND"

        # EMAIL_REPLY — single-word exact
        if _re.match(r"^(reply|respond)[\s!.,]*$", t):
            return "EMAIL_REPLY"

        # EMAIL_REPLY — multi-word patterns
        email_reply_patterns = [
            r"\b(reply|respond|response|draft|compose|answer|write|tell)\b.{0,40}"
            r"\b(to|to\s+the)\b",
            r"\breply\b.{0,30}\b(email|mail|message)\b",
            r"\b(draft|compose)\b.{0,30}\b(a\s+)?(reply|response|answer)\b",
            r"\b(respond|reply)\s+(to|back)\b",
            r"\b(reply\s+to|respond\s+to|draft\s+a\s+reply\s+to"
            r"|compose\s+a\s+reply\s+to)\b",
            r"\b(draft|write|compose)\b.{0,30}\b(response|reply|answer|message)\b",
            # Context follow-ups
            r"\breply\b.{0,40}\b(first|second|latest|that|the|this|above|previous"
            r"|last|from)\b" + _EMAIL_W,
            r"\b(reply\s+to|respond\s+to)\b.{0,60}\b(first|second|latest|last|recent"
            r"|above|that|this)\b",
            r"\b(reply\s+to|respond\s+to)\b.{0,60}\b" + _EMAIL_W
            + r"\b.{0,30}\b(from|regarding|about)\b",
            r"\b(give.*reply|give.*response|write.*back)\b",
            # Typo-tolerant: "give reponce/repsond/reponse to/for ..."
            r"\bgive\b.{0,15}\brep(?!ort)(?!lac)\w+\b.{0,5}\b(to|for)\b",
            # Typo-tolerant: "repsond to above mail"
            r"\brep(?!ort)(?!lac)\w+\b.{0,30}\b(above|that|this)\b.{0,20}"
            r"\b(mail|email|message)\b",
        ]
        for pat in email_reply_patterns:
            if _re.search(pat, t, _re.IGNORECASE):
                if not _re.search(
                    r"\b(and\s+send|then\s+send|now\s+send|immediately\s+send)\b", t
                ):
                    return "EMAIL_REPLY"

        # Short one-word chat replies (after email checks to not capture confirmations)
        if _re.match(
            r"^(yeah|no|nope|thanks|thank\s+you|got\s+it|cool|nice|alright|nah"
            r"|yep|yup|bye)[\s!.,]*$",
            t,
        ):
            return "CHAT"

        # Time / date
        if _re.search(r"\b(what\s+time\s+is\s+it|current\s+time|tell\s+me\s+the\s+time)\b", t):
            return "TIME"
        if _re.search(
            r"\b(what('?s|\s+is)\s+today'?s?\s+date|today'?s\s+date|current\s+date)\b", t
        ):
            return "DATE"

        # Audio
        _AUDIO_EXT = r"\.(?:mp3|wav|m4a|mp4|ogg|flac|webm)"
        if _re.search(r"\b(transcribe|transcription)\b", t) or \
                _re.search(r"\b(convert|index|process)\b.{0,30}" + _AUDIO_EXT, t):
            return "AUDIO_TRANSCRIBE"
        if _re.search(_AUDIO_EXT, t) and _re.search(
            r"\b(what|who|when|where|how|tell|find|search|query"
            r"|summarize|discuss|mention|said|talked|spoke)\b",
            t,
        ):
            return "AUDIO_QUERY"
        if _re.search(r"\b(ask|query|search|find)\b.{0,30}\b(audio|transcript|recording)\b", t):
            return "AUDIO_QUERY"
        if _re.search(
            r"\b(list|show|what|which)\b.{0,40}\b(audio\s+files?|recordings?|transcripts?"
            r"|transcribed)\b",
            t,
        ) or _re.search(
            r"\b(audio|recordings?|transcripts?)\s*(files?|list|available|indexed|stored)\b", t
        ):
            return "AUDIO_LIST"

        # Document listing — checked BEFORE email-search to prevent misclassification.
        # Guard: skip this block entirely when explicit email-domain keywords are present.
        _HAS_EMAIL_KW = bool(_re.search(
            r"\b(email|mail|inbox|gmail|outlook)\b", t
        ))
        _DOC_VERB = (
            r"(list|show|what|which|display|give\s+me|tell\s+me|find|get"
            r"|read|summarize|summarise|open|fetch)"
        )
        _DOC_NOUN = (
            r"(files?|documents?|docs?|pdfs?|reports?|folder|directory"
            r"|available|indexed|uploaded|stored)"
        )
        if not _HAS_EMAIL_KW and (
            _re.search(
                r"\b" + _DOC_VERB + r"\b.{0,50}\b" + _DOC_NOUN + r"\b",
                t,
            )
            or _re.search(
                r"\b(what|which)\b.{0,20}\b(files?|documents?|docs?)\b.{0,30}"
                r"\b(available|indexed|uploaded|stored|have|do\s+you\s+have)\b",
                t,
            )
            or _re.search(
                r"\b(available|indexed|uploaded|stored)\b.{0,20}\b(files?|documents?|docs?)\b",
                t,
            )
            or _re.search(
                r"\b(files?|documents?|docs?)\b.{0,40}\b(from|in|at|inside|under)\b",
                t,
            )
        ):
            if not _re.search(
                r"\b[\w][\w\-\.]*\.(?:pdf|docx|pptx|txt|md|csv|xlsx|png|jpg|jpeg|json)\b", t
            ):
                return "DOCUMENT_LIST"

        # EMAIL SEARCH / SUMMARY — checked AFTER reply/send to avoid misclassification
        _EMAIL_W2 = r"(?:mail(?:s|box)?|email(?:s|box)?|inbox|messages?)"
        _is_email_search = (
            _re.search(
                r"\b(find|search|get|show|give|fetch|list|check|read|pull"
                r"|display|what(?:\s+is)?|which)\b.{0,60}\b" + _EMAIL_W2 + r"\b",
                t,
            )
            or _re.search(
                r"\b" + _EMAIL_W2 + r"\b.{0,60}\b"
                r"(find|search|received|today|yesterday|recent|latest|newest|unread)\b",
                t,
            )
            or _re.search(
                r"\b(recent(?:ly)?|latest|newest|yesterday|today"
                r"|last\s+(?:week|day|month))\b.{0,40}\b(" + _EMAIL_W2 + r"|received)\b",
                t,
            )
            or _re.search(
                r"\b" + _EMAIL_W2 + r"\b.{0,40}\b"
                r"(recent(?:ly)?|latest|newest|yesterday|today)\b",
                t,
            )
        )
        if _is_email_search:
            # Guard: "give rep* to/for" is EMAIL_REPLY, not search
            if _re.search(
                r"\bgive\b.{0,15}\brep(?!ort)(?!lac)\w+\b.{0,5}\b(to|for)\b", t
            ):
                return "EMAIL_REPLY"
            if _re.search(
                r"\b(summarize|summary|all|entire|full)\b.{0,30}\b"
                r"(inbox|all\s+" + _EMAIL_W2 + r")\b",
                t,
            ):
                return "EMAIL_SUMMARY"
            return "EMAIL_SEARCH"

        # Windows filesystem path without reminder keywords -> GENERAL
        if _re.search(r"[A-Za-z]:\\[\\\w\s.\-]+", t):
            if not _re.search(
                r"\b(remind|reminder|alert|alarm|notify|set\s+a|add\s+a|schedule)\b", t
            ):
                return "GENERAL"

        return None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
intent_classifier = IntentClassifier()
