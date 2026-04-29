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
from configs.llm_config import MODEL

log = get_logger(__name__)

_VALID_INTENTS: frozenset[str] = frozenset({
    "GREETING", "TIME", "DATE",
    "REMINDER_SET", "REMINDER_LIST", "REMINDER_DELETE",
    "EMAIL_SUMMARY", "EMAIL_SUMMARIZE", "EMAIL_QUERY",
    "EMAIL_SEARCH", "EMAIL_REPLY", "EMAIL_SEND",
    "DOCUMENT_LIST", "DOCUMENT_SEARCH", "SUMMARY", "RETRIEVAL",
    "FILE_SEARCH", "FILE_LIST", "FILE_SELECT",
    "AUDIO_TRANSCRIBE", "AUDIO_QUERY", "AUDIO_LIST",
    "COMPARE", "CHAT", "GENERAL",
    "MEMORY_STORE", "MEMORY_RECALL", "INVALID_INPUT",
})

_SYSTEM_PROMPT = """You classify user messages into intent labels. Respond ONLY with JSON.

Format (required):
{"intent": "INTENT_NAME", "confidence": 95}

Valid intent names (pick exactly one):
GREETING, TIME, DATE, CHAT, GENERAL,
RETRIEVAL, SUMMARY, DOCUMENT_LIST,
EMAIL_SUMMARY, EMAIL_SUMMARIZE, EMAIL_QUERY, EMAIL_SEARCH, EMAIL_REPLY, EMAIL_SEND,
REMINDER_SET, REMINDER_LIST, REMINDER_DELETE,
AUDIO_TRANSCRIBE, AUDIO_QUERY, AUDIO_LIST,
COMPARE

Key classification rules:
- EMAIL_REPLY     : reply / respond / draft a reply / give response / write back / compose reply
- EMAIL_SEND      : send it / go ahead / yes send / OK send / send the reply / send the email
- EMAIL_SEARCH    : find emails / search inbox / show emails from [sender] / check my emails
- EMAIL_SUMMARIZE : summarize this email / what is this email about / what does it say / give me the gist
- EMAIL_QUERY     : factual questions about an email — who sent it / when / what is the subject
- REMINDER_SET    : remind me at / set a reminder / alert me when / in X minutes
- RETRIEVAL       : questions about document CONTENT (what does X say / explain X / search in Y)
- FILE_SEARCH     : find / locate / open / show a specific file or document by name (no content needed)
- FILE_LIST       : list all files / show all files / what files are available
- DOCUMENT_SEARCH : find / open / locate a specific file or document by name or keyword
- GENERAL         : open questions, comparisons, general knowledge, system/project queries

CRITICAL: Classify intent based ONLY on the current query.
Ignore previous conversation messages unless the query EXPLICITLY refers to them
(e.g. "reply to that email", "summarize it", "send it").
Do NOT return EMAIL_* intent unless the current query contains clear email keywords
(email, mail, inbox, reply, send, draft, compose, message).
Do NOT return FILE_SEARCH / FILE_LIST unless the query is about finding or listing files.

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
        return MODEL

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
        # Strip noise characters (bullet symbols, decorators) that confuse classification.
        text = re.sub("[\u2022\u25cf\u25c6\u25b6\u25ba\u25b8\u25b7\u25e6\u2023\u2043]", "", text).strip()
        if not text:
            return "GENERAL"

        # Pre-step 0: MEMORY/INVALID fast-exit — must run BEFORE the anaphoric
        # context-followup check at step 0, because _FOLLOWUP_PATTERN contains
        # \bthat\b which hijacks "Remember that my favorite language is Python"
        # and routes it to RETRIEVAL before the MEMORY_STORE regex ever fires.
        _pre_memory = self._regex_fastpath(text)
        if _pre_memory in {"MEMORY_STORE", "MEMORY_RECALL", "INVALID_INPUT"}:
            log.info("[INTENT] pre-step0-fastpath -> %s | %r", _pre_memory, text[:60])
            return _pre_memory

        # 0. Anaphoric document follow-up short-circuit (RETRIEVAL / SUMMARY)
        # Resolve effective last_file: actual last_file OR selected_file from memory
        _eff_last_file = last_file
        if not _eff_last_file:
            try:
                from memory.conversation_memory import conversation_memory as _cm0
                _eff_last_file = _cm0.get_selected_file() or None
            except Exception:
                pass

        # ── 0.0a. Summarize/explain + anaphoric ref ("above document",
        #    "that file", "this") — fires even when no last_file is known,
        #    because pending_documents or selected_file may hold the target.
        _is_summarize = bool(re.search(
            r"\b(?:summ(?:ar|er)i[szwe]\w*|summary|explain|overview|gist|describe)\b",
            text, re.IGNORECASE,
        ))
        _has_anaphoric = bool(_FOLLOWUP_PATTERN.search(text))
        if _is_summarize and _has_anaphoric:
            if _eff_last_file:
                log.info("[INTENT] context-followup -> SUMMARY (eff_last_file=%r)", _eff_last_file)
                return "SUMMARY"
            # Check pending_documents in memory — user said "summarize above"
            # after a file listing was returned.
            try:
                from memory.conversation_memory import conversation_memory as _cm0b
                if _cm0b.get_pending_documents() or _cm0b.get_pending_file_selection():
                    log.info("[INTENT] context-followup -> SUMMARY (pending_documents)")
                    return "SUMMARY"
            except Exception:
                pass

        if _eff_last_file:
            # Explicit summarize request — checked FIRST so "summarize it" returns
            # SUMMARY rather than RETRIEVAL (which _FOLLOWUP_PATTERN would produce).
            if _is_summarize:
                log.info("[INTENT] context-followup -> SUMMARY (eff_last_file=%r)", _eff_last_file)
                return "SUMMARY"
            if last_intent in {"RETRIEVAL", "SUMMARY", "GENERAL", "FILE_SEARCH", "FILE_SELECT"} \
                    and _has_anaphoric:
                log.info("[INTENT] context-followup -> RETRIEVAL (eff_last_file=%r)", _eff_last_file)
                return "RETRIEVAL"

        # 0.1. Email context — anaphoric informational follow-up
        # "What is it about?" / "Summarize it" / "What is the subject?" should
        # route to EMAIL_SUMMARIZE or EMAIL_QUERY.  Fires when EITHER:
        #   a) last_intent was an email-related intent (fast, no memory access), or
        #   b) memory.last_email is set (robust to stale/missing last_intent).
        _email_ctx_active = last_intent in {
            "EMAIL_SEARCH", "EMAIL_SUMMARIZE", "EMAIL_QUERY", "EMAIL_SUMMARY", "EMAIL_REPLY"
        }
        # Memory fallback: only activate email context when the query carries an
        # explicit email keyword OR an anaphoric+semantic signal. This prevents
        # stale email data in memory from bleeding into unrelated queries like
        # "find my resume" or "which model is using in my project".
        if not _email_ctx_active and self._EMAIL_RELEVANT_SIGNAL_RE.search(text):
            try:
                from memory.conversation_memory import conversation_memory
                _email_ctx_active = bool(conversation_memory.get_last_email())
            except Exception:
                pass
        if _email_ctx_active:
            email_info = self._email_info_followup(text)
            if email_info:
                log.info("[INTENT] email-info-followup -> %s | %r", email_info, text[:60])
                return email_info

        # 0.2. Draft-modification follow-up
        # "Make it short" / "Rewrite it" / "Use bullet points" after EMAIL_REPLY
        # should re-invoke EMAIL_REPLY with the modified phrasing, not fall to GENERAL.
        if last_intent == "EMAIL_REPLY" and self._DRAFT_MODIFY_RE.search(text):
            log.info("[INTENT] draft-modify-followup -> EMAIL_REPLY | %r", text[:60])
            return "EMAIL_REPLY"

        # 0.3. File-index lookup — fires when a registered filename appears in the
        # query.  Enables "summarize AiAgent.txt" / "open spring.txt" WITHOUT a
        # folder path.  Runs before the email overrides so a file query can never
        # be accidentally misdirected into the email flow.
        _fi_intent = self._file_index_lookup(text)
        if _fi_intent:
            log.info("[INTENT] file-index-lookup -> %s | %r", _fi_intent, text[:60])
            return _fi_intent

        # 0.4. Pre-classification guard — escapes email context override for
        # high-confidence non-email signals (e.g., reminder commands while
        # last_intent is EMAIL_REPLY or EMAIL_SEARCH).
        pre = self._pre_classify_guard(text)
        if pre:
            log.info("[INTENT] pre-classify-guard -> %s | %r", pre, text[:60])
            return pre

        # 0.5a. Strong document-search pre-emption — fires BEFORE the email
        # context override so "find my resume" can never be misrouted as email.
        doc_pre = self.detect_document_intent(text)
        if doc_pre:
            log.info("[INTENT] strong-doc-rule -> %s | %r", doc_pre, text[:60])
            return doc_pre

        # 0.5b. Strong general-query pre-emption — for unambiguous system /
        # knowledge queries like "which model is using in my project".
        gen_pre = self.detect_general_intent(text)
        if gen_pre:
            log.info("[INTENT] strong-general-rule -> %s | %r", gen_pre, text[:60])
            return gen_pre

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

        # 3. LLM classification — DISABLED
        # PART 2: REMOVE LLM-BASED INTENT CLASSIFICATION
        llm_result = None
        confidence = 0
        
        # --- LIGHT SANITY CHECK ---
        ui_lower = text.lower()
        file_signals = ["find", "document", "file", "resume", "feedback", "pdf", "report", "csv", "internship", "data", "search", "retrieve", "fetch", "locate"]
        has_file_signal = any(s in ui_lower for s in file_signals)

        if has_file_signal:
            log.info("[INTENT-FIX] Signal detected -> FILE_SEARCH | %r", text[:60])
            return "FILE_SEARCH"

        # 4. Final fallback — return GENERAL if LLM failed or returned invalid intent
        log.info("[INTENT] LLM failed or invalid intent, defaulting to GENERAL | %r", text[:60])
        return "GENERAL"

    # ── pre-classification guard ──────────────────────────────────────────
    # These patterns identify signals so unambiguous that they must escape
    # the email context override and email guardrail entirely.

    # Inputs matching this pattern are NEVER overridden by email context.
    # Reminder commands and time-relative expressions escape the email flow.
    _OVERRIDE_EXEMPT_RE = re.compile(
        r"\b(remind(?:er)?s?|set\s+(?:a\s+)?(?:reminder|alarm|alert)"
        r"|alert\s+me|notify\s+me"
        r"|in\s+\d+\s+(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|days?)"
        r"|summarize|summarise|explain|overview|gist)\b",
        re.IGNORECASE,
    )

    # Patterns that disqualify a statement from being email reply body content.
    # Catches WH-question starters (even without ?) and reminder/domain terms.
    _REPLY_CONTENT_EXEMPT_RE = re.compile(
        r"^(what|why|how|when|who|where|which|is|are|was|were|does|did"
        r"|can|could|would|should|tell\s+me\b|explain)\b"
        r"|\b(remind(?:er)?|set\s+(?:a\s+)?(?:reminder|alarm|alert)"
        r"|alert\s+me|notify\s+me"
        r"|in\s+\d+\s+(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|days?)"
        r"|summarize|summarise|summary|overview|gist)\b",
        re.IGNORECASE,
    )

    # Used by _pre_classify_guard: unambiguous reminder signals.
    _REMINDER_FASTPATH_RE = re.compile(
        r"\b(remind\s+me\b|set\s+(?:a\s+)?(?:reminder|alarm)|add\s+(?:a\s+)?reminder"
        r"|create\s+(?:a\s+)?reminder|reminder\s+(?:for|at|in|to|about)"
        r"|alert\s+me\b|notify\s+me\b"
        r"|in\s+\d+\s+(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|days?))\b",
        re.IGNORECASE,
    )

    # ── email context info follow-up (step 0.1) ────────────────────────────
    # These patterns detect anaphoric informational questions about the email
    # currently in context ("What is it about?", "Summarize it", "Who sent it?").
    # Used in step 0.1 of classify() — fires ONLY when last_intent is email-related.

    # Matches informational questions/requests with anaphoric reference.
    # Requires either a summarize/explain verb + anaphoric ref, or a WH-word +
    # anaphoric ref, or anaphoric ref + informational verb ("it ... about").
    # Also matches implicit summary requests that don't need an anaphoric word
    # (e.g. bare "summarize", "give me an overview").
    _EMAIL_ANAPHORIC_INFO_RE = re.compile(
        r"(?:\b(?:summarize|summarise|explain|describe|overview|gist|brief\s+me|tell\s+me)\b"
        r".{0,40}\b(?:it|this|that|the\s+(?:email|mail)|above)\b)"
        r"|(?:\b(?:what|who|when|where|why)\b"
        r".{0,30}\b(?:it|this|that|the\s+(?:email|mail)|above\s+(?:email|mail)?)\b)"
        r"|(?:\b(?:it|this|that)\b.{0,20}\b(?:about|contain|say|mean|says?|means?|talk)\b)"
        # Implicit summary requests (no anaphoric word required when email is in memory)
        r"|(?:^\s*(?:summarize|summarise|explain|brief\s+summary|give\s+(?:me\s+)?(?:a\s+)?(?:summary|overview|gist|recap))\s*[?!.,]?\s*$)",
        re.IGNORECASE,
    )

    # Signals that the question is about email metadata (not content summary).
    # Also matches direct (non-anaphoric) metadata questions such as
    # "What is the subject?" and "Who sent this?".
    _EMAIL_ANAPHORIC_META_RE = re.compile(
        r"\bwho\s+sent\b|\bwho\s+(?:is|was)\s+(?:it|this|that)\s+from\b"
        r"|\bsender\b|\bfrom\s+(?:whom|who)\b"
        r"|\bwhen\b.{0,30}\b(?:it|this|that|email|mail)\b.{0,20}\b(?:sent|received|arrive)\b"
        r"|\b(?:subject|topic|title)\b.{0,30}\b(?:this|the|it|that)\b"
        # Direct non-anaphoric metadata questions
        r"|\bwhat\s+(?:is|was)\s+the\s+(?:subject|topic|title)\b"
        r"|\bwhat\s+(?:is|was)\s+(?:the\s+)?(?:sender|from\s+address)\b"
        r"|\bwho\s+is\s+(?:the\s+)?(?:sender|author)\b"
        r"|\bwho\s+(?:sent|wrote|emailed)\s+(?:this|it|the\s+(?:email|mail))?\b"
        r"|\bwhen\s+(?:was|is)\s+(?:it\s+)?(?:sent|received|arrived?)\b",
        re.IGNORECASE,
    )

    # Time/date/weather queries — excluded so "What time is it?" is never caught
    # as an email info follow-up even when last_intent is EMAIL_SEARCH.
    _EMAIL_ANAPHORIC_EXCL_RE = re.compile(
        r"\bwhat\s+time\b|\btime\s+is\s+it\b|today(?:'?s)?\s+date\b"
        r"|\bcurrent\s+(?:time|date)\b|\bweather\b|\btemperature\b",
        re.IGNORECASE,
    )

    # Explicit document/file reference — prevents email context from absorbing
    # queries that are clearly about a file, not an email.
    _DOC_CTX_EXCL_RE = re.compile(
        r"\b(files?|documents?|docs?|folder|directory"
        r"|resume|curriculum\s+vitae|\bcv\b|report|invoice|contract"
        r"|proposal|portfolio|spreadsheet|presentation|slides?)\b"
        r"|\.(?:txt|pdf|docx?|pptx?|md|csv|xlsx?|json|log)\b",
        re.IGNORECASE,
    )
    _EMAIL_NOUN_RE = re.compile(
        r"\b(email|mail|inbox|message)\b",
        re.IGNORECASE,
    )

    # ── Strong pre-LLM detection patterns (Steps 0.5a / 0.5b) ────────────────
    # High-confidence document/file-search signals — fires BEFORE LLM and
    # BEFORE email-context override so document queries can never be absorbed.
    _STRONG_DOC_RE = re.compile(
        r"(?:"
        # Action verb + document-like noun (not an email noun)
        r"\b(?:find|open|locate|search\s+for|look\s+for|show\s+me|get\s+me"
        r"|retrieve|fetch|access|load|pull\s+up)\b"
        r".{0,50}"
        r"\b(?:resume|cv|curriculum\s+vitae|report|invoice|contract|proposal"
        r"|notes?|manual|guide|handbook|slides?|presentation|spreadsheet"
        r"|budget|plan|schedule|log|portfolio|brochure|certificate"
        r"|deed|receipt|letter)\b"
        r"|"
        # Explicit file extension in query
        r"\b[\w][\w\s\-]*\.(?:pdf|docx?|pptx?|txt|md|csv|xlsx?|json|log)\b"
        r"|"
        # Action verb + generic file/document noun
        r"\b(?:find|open|locate|show|get|read|access|load|fetch)\b"
        r".{0,30}\b(?:file|document|doc|folder|directory)\b"
        r")",
        re.IGNORECASE,
    )

    # Discovery-specific verbs — ONLY match when the intent is to FIND/LOCATE a file
    # (not to read its content).  Returns FILE_SEARCH, not RETRIEVAL.
    # NOTE: "list" and "show" are intentionally excluded here; they are handled
    # by _FILE_LIST_RE below which returns FILE_LIST instead.
    _FILE_DISCOVERY_RE = re.compile(
        r"(?:"
        # Pattern A: discovery verb + file/document noun (original + bare "show")
        r"\b(?:find|locate|search\s+(?:for)?|look\s+for|open|get\s+me|show(?:\s+me)?)\b"
        r".{0,60}"
        r"\b(?:resume|cv|curriculum\s+vitae|report|invoice|contract|proposal"
        r"|notes?|manual|guide|handbook|slides?|presentation|spreadsheet"
        r"|budget|plan|log|portfolio|brochure|certificate"
        r"|deed|receipt|letter|files?|documents?|docs?)\b"
        r"|"
        # Pattern B: "any doc(s)/document(s) about/related to/on ..."
        r"\bany\s+(?:docs?|documents?|files?)\b.{0,40}"
        r"\b(?:about|on|related\s+to|for|covering|regarding)\b"
        r"|"
        # Pattern C: "documents/docs related to / about / for ..."
        r"\b(?:docs?|documents?|files?)\s+(?:related\s+to|about|for|on|regarding)\b"
        r"|"
        # Pattern D: "documentation for / about ..."
        r"\bdocumentation\s+(?:for|about|on|related\s+to)\b"
        r"|"
        # Pattern E: bare search query — "find ... related to" or "search ... about"
        r"\b(?:find|search|look\s+for|locate)\b.{0,40}"
        r"\b(?:related\s+to|about|on\s+the\s+topic\s+of|covering|regarding)\b"
        r")",
        re.IGNORECASE,
    )

    # Discovery verbs without a specific noun — matches filesystem listing requests.
    # Returns FILE_LIST (triggers metadata listing, not a keyword search).
    _FILE_LIST_RE = re.compile(
        # Only allow filler words (me/all/my/the/available) between verb and noun —
        # topic words in between (e.g. "show app flow document") must NOT match here.
        r"\b(?:list|show|display)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:the\s+)?(?:available\s+)?(?:files?|documents?)\b"
        r"|\b(?:files?|documents?)\b.{0,30}\b(?:available|indexed|stored|uploaded)\b",
        re.IGNORECASE,
    )

    # Discovery verbs for bare extension queries ("locate report.pdf", "open notes.txt")
    _FILE_DISC_VERB_RE = re.compile(
        r"\b(?:find|locate|search\s+for|look\s+for|open|get\s+me|show\s+me)\b",
        re.IGNORECASE,
    )

    # Content question aimed at a specific named document:
    #   "what does the report say about Q4"
    #   "how does the manual explain installation"
    # Returns RETRIEVAL (go to RAG) rather than falling through to LLM.
    _DOC_SPECIFIC_NOUNS = (
        r"(?:report|resume|cv|invoice|contract|proposal|notes?|manual|guide"
        r"|handbook|slides?|presentation|spreadsheet|budget|plan|schedule|log"
        r"|portfolio|brochure|certificate|deed|receipt|letter|document|doc)"
    )
    _CONTENT_ON_DOC_RE = re.compile(
        # Pattern A: question word + doc noun + content verb (verb comes after noun)
        # e.g. "what does the report say about Q4"
        r"\b(?:what|how|when|where|why|who|which)\b"
        r".{0,40}\b" + _DOC_SPECIFIC_NOUNS + r"\b"
        r".{0,40}\b(?:says?|tells?|contains?|discuss(?:es)?|mentions?"
        r"|means?|explains?|shows?|about|cover|address)\b"
        r"|"
        # Pattern B: what/how + content noun + preposition + doc noun (verb before noun)
        # e.g. "what information is in the manual", "how setup works in the guide"
        r"\b(?:what|how)\b.{0,30}\b(?:information|info|details?|content|data|steps?|instruction)\b"
        r".{0,30}\b(?:in|from|inside|within|about)\b.{0,20}\b" + _DOC_SPECIFIC_NOUNS + r"\b",
        re.IGNORECASE,
    )

    # High-confidence general/system/knowledge signals — fires BEFORE LLM.
    # "which model is using in my project", "what version of python", etc.
    _STRONG_GENERAL_RE = re.compile(
        r"\b(?:which|what)\b.{0,25}"
        r"\b(?:model|version|framework|library|tool|setting|config"
        r"|configuration|parameter|dependency|package|module|algorithm)\b"
        r".{0,40}"
        r"\b(?:using|used|set|installed|configured|in|for|my|this|project"
        r"|code|app|system|codebase|repo|repository)\b",
        re.IGNORECASE,
    )

    # Broad general-knowledge question patterns — "what is X", "explain X",
    # "how does X work", "tell me about X".  Fires in classify_query() and
    # detect_general_intent() when NO file/document/email signal is also present.
    _GENERAL_KNOWLEDGE_RE = re.compile(
        r"^(?:"
        r"what\s+(?:is|are|was|were)\s+(?!my\s+)"  # "what is AI" (not "what is my ...")
        r"|explain\s+"
        r"|define\s+"
        r"|how\s+(?:does|do|can|would|should|is|are)\s+"
        r"|why\s+(?:is|are|does|do|was|were)\s+"
        r"|tell\s+me\s+about\s+"
        r"|what\s+do\s+you\s+(?:know|think)\s+(?:about\s+)?"
        r"|describe\s+(?!(?:the\s+)?(?:file|document|doc)\b)"
        r")",
        re.IGNORECASE,
    )

    # Explicit file/doc/personal-data reference — prevents GENERAL_KNOWLEDGE on
    # queries that mention personal files: "my resume", "in the document", etc.
    _FILE_PERSONAL_REF_RE = re.compile(
        r"\b(?:in\s+(?:this|the|my|that)\s+(?:file|doc(?:ument)?|report|pdf)"
        r"|from\s+(?:this|the|my)\s+(?:file|doc(?:ument)?)"
        r"|my\s+(?:cgpa|grade|marks?|score|gpa|salary|resume|cv|report)\b)",
        re.IGNORECASE,
    )

    # Email keywords required for EMAIL_* intents to be considered valid.
    _EMAIL_CONTENT_KW_RE = re.compile(
        r"\b(?:email|mail|inbox|reply|send|message|draft|compose"
        r"|subject|sender|recipient|attachment)\b",
        re.IGNORECASE,
    )

    # Minimal signal required before the email-context memory fallback fires.
    # Prevents stale email in memory from bleeding into unrelated queries.
    _EMAIL_RELEVANT_SIGNAL_RE = re.compile(
        r"\b(?:email|mail|inbox)"
        r"|\b(?:summarize|summarise|explain|describe|overview|gist)\b"
        r"|\b(?:it|this|that|above)\b.{0,15}\b(?:about|contain|say|mean)"
        r"|\b(?:reply|respond|send\s+it|compose|draft)\b",
        re.IGNORECASE,
    )

    def _email_info_followup(self, text: str) -> Optional[str]:
        """Detect anaphoric informational questions about the email in context.

        Returns EMAIL_SUMMARIZE or EMAIL_QUERY when text is an informational
        question that uses "it/this/that/the email" to refer to the email
        currently in conversation context, OR when it is a direct metadata
        question (e.g. "What is the subject?", "Who is the sender?").

        Returns None when the text is not an informational question or contains
        time/date/weather terms that should go through the normal pipeline.
        """
        if self._EMAIL_ANAPHORIC_EXCL_RE.search(text):
            return None
        # Don't capture queries that are clearly about a file/document, not an email.
        # e.g. "summarize the above file" should not become EMAIL_SUMMARIZE.
        if self._DOC_CTX_EXCL_RE.search(text) and not self._EMAIL_NOUN_RE.search(text):
            return None
        # Check META first — direct metadata questions like "What is the subject?"
        # or "Who is the sender?" are unambiguous and don't need an anaphoric word.
        if self._EMAIL_ANAPHORIC_META_RE.search(text):
            return "EMAIL_QUERY"
        # For summary/content requests, require either an anaphoric reference
        # or a bare summarize verb (to avoid false positives).
        if not self._EMAIL_ANAPHORIC_INFO_RE.search(text):
            return None
        return "EMAIL_SUMMARIZE"

    def _pre_classify_guard(self, text: str) -> Optional[str]:
        """Return a high-confidence non-email intent to escape email context override.

        Runs between the anaphoric follow-up check (step 0) and the email context
        override (step 0.5) in ``classify``.  Only fires for signals that are so
        unambiguous they would otherwise be hijacked by the email override or
        context guardrail — e.g., reminder commands when last_intent==EMAIL_REPLY.
        """
        if self._REMINDER_FASTPATH_RE.search(text):
            return "REMINDER_SET"
        return None

    # ── strong pre-LLM rule detectors (steps 0.5a / 0.5b) ─────────────────

    def detect_document_intent(self, text: str) -> Optional[str]:
        """Return FILE_LIST, FILE_SEARCH, RETRIEVAL, or SUMMARY for document queries.

        Priority order:
        1. FILE_LIST    — "list/show (all) (my) files" — no specific file named.
        2. FILE_SEARCH  — discovery verb (find/locate/open) + file noun or extension.
           No RAG. The orchestrator searches the metadata index.
        3. SUMMARY      — summarize verb + file-extension or document noun.
        4. RETRIEVAL    — other content queries matching _STRONG_DOC_RE.

        Fires BEFORE the LLM and BEFORE the email-context override.
        Returns None when email keywords are also present.
        """
        if self._EMAIL_NOUN_RE.search(text):
            return None

        # Guard: summarize/explain + anaphoric reference ("above", "that", "this")
        # should route to SUMMARY, NOT FILE_SEARCH — even if _FILE_DISCOVERY_RE
        # or _FILE_LIST_RE would match the word "document"/"file".
        _has_summarize = bool(re.search(
            r"\b(?:summ(?:ar|er)i[szwe]\w*|summary|explain|overview|gist|describe)\b",
            text, re.IGNORECASE,
        ))
        _has_anaphoric_ref = bool(re.search(
            r"\b(?:above|that|this|previous|last|same|it)\b",
            text, re.IGNORECASE,
        ))
        if _has_summarize and _has_anaphoric_ref:
            return "SUMMARY"

        # 1. Generic file listing request
        if self._FILE_LIST_RE.search(text):
            return "FILE_LIST"
        # 2. Discovery query
        if self._FILE_DISCOVERY_RE.search(text):
            return "FILE_SEARCH"
        # 2b. Discovery verb + explicit file extension (e.g. "locate report.pdf")
        if self._STRONG_DOC_RE.search(text) and self._FILE_DISC_VERB_RE.search(text):
            if re.search(r"\bsumm(?:ar|er)i[szwe]\w*\b", text, re.IGNORECASE):
                return "SUMMARY"
            return "FILE_SEARCH"
        # 3. Content question about a specific named document
        #    e.g. "what does the report say about Q4"
        if self._CONTENT_ON_DOC_RE.search(text):
            return "RETRIEVAL"
        # 4. Summarize/retrieve a document
        if self._STRONG_DOC_RE.search(text):
            if re.search(r"\bsumm(?:ar|er)i[szwe]\w*\b", text, re.IGNORECASE):
                return "SUMMARY"
            return "RETRIEVAL"
        return None

    def detect_general_intent(self, text: str) -> Optional[str]:
        """Return GENERAL when *text* is a clear system/knowledge/project query.

        Covers:
        - System/config queries: "which model is in my project", "what Python version"
        - General knowledge questions: "what is AI", "explain machine learning",
          "how does TCP work", "tell me about neural networks"

        Returns None when email keywords are present or a file/doc signal exists.
        """
        if self._EMAIL_NOUN_RE.search(text):
            return None
        if self._STRONG_GENERAL_RE.search(text):
            return "GENERAL"
        # Broad general-knowledge patterns ("what is X", "explain X", …)
        # Only fire when there is NO file/document/personal-data reference.
        if self._GENERAL_KNOWLEDGE_RE.match(text):
            if not self._FILE_PERSONAL_REF_RE.search(text):
                # Guard against document nouns: "what does the report say",
                # "explain my resume" etc. — these already caught by detect_document_intent.
                if not self.detect_document_intent(text):
                    return "GENERAL"
        return None

    def classify_query(self, query: str) -> str:
        """3-layer hybrid query classifier — GENERAL_KNOWLEDGE / FILE_SEARCH / FILE_QA / PIPELINE.

        Designed as a fast pre-emption gate called by the orchestrator BEFORE
        the full intent pipeline.  Returns one of four labels:

        - "GENERAL_KNOWLEDGE" → route directly to pure LLM; skip all file ops.
        - "FILE_SEARCH"        → route to metadata index search; no RAG.
        - "FILE_QA"            → route to file content Q&A / RAG.
        - "PIPELINE"           → let the main intent pipeline decide (default).

        Examples
        --------
        classify_query("What is artificial intelligence?") → "GENERAL_KNOWLEDGE"
        classify_query("Find my resume")                   → "FILE_SEARCH"
        classify_query("What is my CGPA in resume?")       → "FILE_QA"
        classify_query("Explain machine learning")         → "GENERAL_KNOWLEDGE"
        classify_query("Remind me at 5 PM")                → "PIPELINE"
        classify_query("Reply to the email")               → "PIPELINE"
        """
        q = query.lower().strip()

        # Guard: domain-specific intents handled by the main pipeline
        if re.search(
            r"\b(?:email|mail|inbox|remind(?:er)?|alarm|alert"
            r"|audio|transcript|folder|directory)\b",
            q,
        ):
            return "PIPELINE"

        # Guard: file extension present → file operation
        if re.search(
            r"\b\w[\w\s\-]+\.(?:pdf|docx?|xlsx?|pptx?|txt|csv|md|json|log|py|js|ts)\b",
            q,
        ):
            return "FILE_QA"

        # Layer 1 — FILE_SEARCH / FILE_LIST detection (checked BEFORE personal-ref guard
        # so "find my resume" → FILE_SEARCH, not FILE_QA)
        doc_intent = self.detect_document_intent(q)
        if doc_intent in ("FILE_SEARCH", "FILE_LIST"):
            return "FILE_SEARCH"

        # Guard: personal file-content queries → FILE_QA, not GENERAL
        # Runs after FILE_SEARCH so discovery verbs win ("find my resume" → FILE_SEARCH).
        if self._FILE_PERSONAL_REF_RE.search(q):
            return "FILE_QA"

        # Layer 2 — FILE_QA / RETRIEVAL / SUMMARY detection
        if doc_intent in ("RETRIEVAL", "SUMMARY"):
            return "FILE_QA"

        # Layer 3 — clear general-knowledge patterns
        if self._GENERAL_KNOWLEDGE_RE.match(q):
            return "GENERAL_KNOWLEDGE"

        return "PIPELINE"

    def validate_email_intent(self, text: str, intent: str) -> str:
        """Downgrade EMAIL_* intent returned by the LLM when the query lacks email keywords.

        Post-LLM guard that catches cases where the LLM is misled by email-heavy
        conversation history and classifies an unrelated query (e.g. "find my resume")
        as EMAIL_SUMMARIZE or EMAIL_QUERY.

        Rules:
        - EMAIL_SEND / EMAIL_REPLY: allow with any email-adjacent verb (lenient).
        - Other EMAIL_* (SUMMARIZE, QUERY, SEARCH, SUMMARY): require at least one
          email keyword; if absent, downgrade to RETRIEVAL (document signal) or GENERAL.
        """
        if not intent.startswith("EMAIL_"):
            return intent
        # For send/reply, allow loose signals to avoid breaking legit email flow.
        if intent in {"EMAIL_SEND", "EMAIL_REPLY"}:
            if re.search(
                r"\b(?:reply|respond|send|email|mail|draft|compose|go\s+ahead)\b",
                text, re.IGNORECASE,
            ):
                return intent
            return "GENERAL"
        # For informational/search email intents, require explicit email keyword.
        if not self._EMAIL_CONTENT_KW_RE.search(text):
            log.warning(
                "[INTENT] validate_email_intent: %s -> no email keywords -> override | %r",
                intent, text[:60],
            )
            if self._STRONG_DOC_RE.search(text):
                return "RETRIEVAL"
            return "GENERAL"
        return intent

    # ── file-index lookup (step 0.3) ────────────────────────────────────────
    # Determine doc intent when a known filename is mentioned in the query.

    # Verbs that indicate the user wants a summary / explanation.
    _FI_SUMMARIZE_RE = re.compile(
        r"\b(?:summ(?:ar|er)i[szwe]\w*|explain|describe|overview|gist"
        r"|what\s+(?:is|are|does)\s+(?:it|this|that|the|in)\b)\b",
        re.IGNORECASE,
    )
    # Verbs that indicate the user wants to read / open / retrieve.
    _FI_READ_RE = re.compile(
        r"\b(?:open|read|show|display|view|get|fetch|access|print|load)\b",
        re.IGNORECASE,
    )

    def _file_index_lookup(self, text: str) -> Optional[str]:
        """Return an intent when a registered filename appears in *text*.

        Checks the session file index in ``ConversationMemory``.  If a known
        filename is found in the query text the intent is determined by the
        action verb present:
        - summarize / explain / describe → SUMMARY
        - open / read / show / view … → RETRIEVAL
        - anything else (default) → SUMMARY

        Returns ``None`` when the file index is empty or no filename matches.
        """
        try:
            from memory.conversation_memory import conversation_memory
            file_index = conversation_memory.get_file_index()
            if not file_index:
                return None
            text_lower = text.lower()
            for name in file_index:
                if name in text_lower:
                    if self._FI_SUMMARIZE_RE.search(text):
                        return "SUMMARY"
                    if self._FI_READ_RE.search(text):
                        return "RETRIEVAL"
                    # Default when only the filename is present without a verb
                    return "SUMMARY"
        except Exception as exc:
            log.debug("[INTENT] file-index-lookup failed: %s", exc)
        return None

    # ── email context override (memory-aware) ─────────────────────────────
    # Draft-modification follow-up — when last_intent == EMAIL_REPLY, these
    # phrases indicate the user wants to modify/reformat an existing draft.
    _DRAFT_MODIFY_RE = re.compile(
        r"\b("
        r"make\s+it\s+(short|shorter|brief|concise|formal|informal|friendly|professional)"
        r"|make\s+(it\s+)?longer"
        r"|shorten\s+it|shorten\s+the\s+(reply|email|draft|response|message)"
        r"|keep\s+it\s+(short|brief|concise)"
        r"|rewrite\s+(it|the\s+(reply|email|draft|response|message))?"
        r"|simplify\s+(it|the\s+(reply|email|draft|response|message))?"
        r"|make\s+(it\s+)?more\s+(formal|informal|friendly|professional|polite|concise)"
        r"|change\s+(the\s+)?tone"
        r"|use\s+(bullet|bullets|bullet\s+points|list\s+format)"
        r"|in\s+bullet\s+points?|as\s+a\s+list"
        r"|shorter\s+(version|reply|email|draft|response|message)"
        r")\b",
        re.IGNORECASE,
    )

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

        Catches two kinds of reply signals — both run BEFORE the regex fast-path
        so they cannot be overridden by wrong regex classification:

        Signal A — Reply command/phrase (broad RE): "reply to", "respond", "give response",
                   typos, indirect phrasing.  Only requires email context in memory.
        Signal B — Reply-content statement: "I will be available", "I am working on it".
                   Requires BOTH email context AND last_intent in email flow so we don't
                   accidentally intercept non-email first-person statements.
        """
        # Guard: strong non-email signals must never be hijacked by email context.
        # Reminder commands and time-relative expressions escape the override.
        if self._OVERRIDE_EXEMPT_RE.search(text):
            log.debug(
                "[INTENT] email-context-override: exempt signal, skipping | %r",
                text[:60],
            )
            return None

        _is_reply_cmd = bool(self._BROAD_REPLY_RE.search(text))
        _is_reply_stmt = (
            last_intent in {"EMAIL_SEARCH", "EMAIL_REPLY"}
            and not _is_reply_cmd
            # Pass email_context=True: we're already in email flow so short
            # non-question statements ("Good morning", "I will be available") qualify.
            and self._is_reply_content_statement(text, email_context=True)
        )

        if not _is_reply_cmd and not _is_reply_stmt:
            return None  # no signal of any kind — let normal pipeline proceed

        try:
            from memory.conversation_memory import conversation_memory

            last_email = conversation_memory.get_last_email()
            search_results = (
                conversation_memory.get_last_email_search_results()
                if not last_email else None
            )
            _has_email_ctx = bool(last_email) or bool(search_results)

            if not _has_email_ctx:
                log.info(
                    "[INTENT] email-context-override: signal present but no email "
                    "in memory — letting normal pipeline decide. input=%r",
                    text[:60],
                )
                return None

            if _is_reply_cmd:
                log.info(
                    "[INTENT] email-context-override: reply-command + email_ctx "
                    "(from=%s) -> EMAIL_REPLY",
                    str(last_email.get("from", "?") if last_email else "search_results")[:40],
                )
            else:
                log.info(
                    "[INTENT] email-context-override: reply-content-statement "
                    "+ email_ctx (last_intent=%s) -> EMAIL_REPLY | %r",
                    last_intent, text[:60],
                )
            return "EMAIL_REPLY"

        except Exception as exc:
            log.debug("[INTENT] email-context-override memory check failed: %s", exc)

        return None

    # ── reply-content detection ────────────────────────────────────────────

    _REPLY_CMD_RE = re.compile(
        r"\b(reply|respond|response|draft|compose|answer|write\s*back"
        r"|send\s+it|go\s+ahead|proceed)\b",
        re.IGNORECASE,
    )
    _QUESTION_RE = re.compile(r"\?")
    # Search/browse commands at the start of text are never reply content.
    _EMAIL_SEARCH_CMD_RE = re.compile(
        r"^(?:find|search|look\s+for|show|list|fetch|get|check|read)\b",
        re.IGNORECASE,
    )
    # Broad set of statement-opening words that indicate reply body content.
    # Covers: first-person, acknowledgements, greetings, affirmatives.
    _CONTENT_STMT_RE = re.compile(
        r"^i\s+(will|am|have|can|'?ll|would|need|was|want|did|think|believe"
        r"|understand|confirm|agree|appreciate|apologize|apologise)\b"
        r"|^(sure|okay|ok|yes|no|sorry|thanks|thank|good|hi|hello|dear"
        r"|we\b|great|noted|understood|absolutely|certainly|definitely"
        r"|unfortunately|happy\s+to|please\b|kindly\b)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_reply_content_statement(cls, text: str, *, email_context: bool = False) -> bool:
        """Return True when *text* looks like user-provided reply body content.

        Parameters
        ----------
        text : str
            Input to evaluate (may be already lowercased).
        email_context : bool
            When True (caller has confirmed email context exists + last_intent is
            in email flow), any short non-question statement (≤8 words) is treated
            as reply content.  This covers short greetings and fragments that would
            otherwise not match the explicit regex.
        """
        t = text.strip().lower()
        if not t:
            return False
        if cls._REPLY_CMD_RE.search(t):
            return False
        if cls._QUESTION_RE.search(t):
            return False
        # Search/browse commands are never reply body content
        if cls._EMAIL_SEARCH_CMD_RE.search(t):
            return False
        # Guard: WH-question starters and non-email domain terms are never reply
        # body content. Prevents "Set reminder in 1 minute" and "What is this email
        # about" from being classified as EMAIL_REPLY when email context is active.
        if cls._REPLY_CONTENT_EXEMPT_RE.search(t):
            return False
        # In email context: any short unambiguous statement qualifies as reply content
        if email_context and len(t.split()) <= 8:
            return True
        return bool(cls._CONTENT_STMT_RE.search(t))

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

        # Guard: reminder/time-relative signals must escape the email guardrail.
        # Without this, "Set reminder in 1 minute" after EMAIL_SEARCH/EMAIL_REPLY
        # would be misrouted via the content-statement path.
        if self._OVERRIDE_EXEMPT_RE.search(t):
            log.debug(
                "[INTENT] guardrail: exempt signal, skipping email guardrail | %r",
                t[:60],
            )
            return None

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

        # After EMAIL_SEARCH or EMAIL_REPLY: user provides reply *content* (a statement).
        # e.g. "I will be available", "Good morning", "I am working on it"
        # These are NOT reply commands but ARE the body the user wants in the reply.
        # email_context=True allows short non-question statements to match.
        if last_intent in {"EMAIL_SEARCH", "EMAIL_REPLY"}:
            if self._is_reply_content_statement(t, email_context=True):
                log.info(
                    "[INTENT] guardrail: last_intent=%s + content statement -> EMAIL_REPLY",
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
            # Only inject history when the query explicitly references prior context
            # (anaphoric pronouns or follow-up signals). Sending email-heavy history
            # with unrelated queries was the primary cause of EMAIL_* misclassification.
            _needs_history = bool(re.search(
                r"\b(?:it|this|that|above|previous|last|the\s+email"
                r"|the\s+file|those|that\s+one|reply\s+to\s+(?:that|it))\b",
                text, re.IGNORECASE,
            ))
            if history and _needs_history:
                messages.extend(history[-3:])
            messages.append({"role": "user", "content": text})

            log.info(
                "[INTENT_CLASSIFIER] LLM call: %d history turns, query=%r",
                len(history or []), text[:60],
            )

            print(f"[LLM] Using model: {MODEL}")
            resp = ollama.chat(
                model=self._model(),
                messages=messages,
                format="json",                      # Force structured JSON output
                options={"temperature": 0.0, "num_predict": 80},
            )
            raw = (resp.get("message", {}).get("content") or "").strip()
            log.info("[INTENT_CLASSIFIER] LLM raw: %s", raw[:150])

            result = self._parse_json_intent_with_confidence(raw)
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
                # FIX: An unknown intent from the LLM is not an error. Map it to GENERAL
                # so it can be handled by the default AI handler instead of being
                # rejected, which causes incorrect fallback behavior.
                log.warning("[INTENT_CLASSIFIER] Unknown intent %r mapped to GENERAL", intent)
                return "GENERAL"
            except _json.JSONDecodeError:
                pass

        # Step 3: try parsing the entire cleaned string
        try:
            data = _json.loads(cleaned)
            if isinstance(data, dict) and "intent" in data:
                intent = str(data["intent"]).strip().upper()
                if intent in _VALID_INTENTS:
                    return intent
                # FIX: Map unknown intents to GENERAL.
                log.warning("[INTENT_CLASSIFIER] Unknown intent %r mapped to GENERAL", intent)
                return "GENERAL"
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

        The small model (llama3) sometimes returns GREETING or CHAT
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

        # EMAIL_* informational/search intents require at least one email keyword.
        # Catches LLM misclassification when history is email-heavy.
        if intent in {"EMAIL_SUMMARIZE", "EMAIL_QUERY", "EMAIL_SUMMARY", "EMAIL_SEARCH"}:
            if not re.search(r"\b(email|mail|inbox|message)\b", t):
                return False

        return True

    # ── heuristic fallback ──────────────────────────────────────────────────

    @staticmethod
    def _heuristic_fallback(text: str, last_intent: Optional[str] = None) -> Optional[str]:
        """Conservative keyword-based fallback when LLM fails.

        Only fires on high-confidence unambiguous signals to avoid false positives.
        """
        t = text.lower().strip()

        # Email reply signal — only when no document-domain or reminder keywords present
        if re.search(r"\b(reply|respond|response)\b", t):
            if not re.search(
                r"\b(files?|documents?|docs?|folder|directory"
                r"|summarize|summarise|list|show\s+files?"
                r"|remind(?:er)?|alarm|alert)\b",
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

        # ----------------------------------------------------------------
        # FILE_LIST — "list my files", "show all files", "what files do I have"
        # Fires before DOCUMENT_LIST so these go to metadata search, not RAG.
        # ----------------------------------------------------------------
        _FILE_LIST_VERBS = r"(list|show|display|what|which)"
        _FILE_LIST_NOUNS = r"(files?|documents?|docs?)"
        if not _HAS_EMAIL_KW and (
            _re.search(
                r"\b" + _FILE_LIST_VERBS + r"\b.{0,30}\ball\b.{0,20}\b" + _FILE_LIST_NOUNS + r"\b",
                t,
            )
            or _re.search(
                r"\b(list|show)\b.{0,20}\b(?:my\s+)?" + _FILE_LIST_NOUNS + r"\b",
                t,
            )
            or _re.search(
                r"\b" + _FILE_LIST_NOUNS + r"\b.{0,30}\b(available|indexed|stored|uploaded)\b",
                t,
            )
        ):
            return "FILE_LIST"

        # ----------------------------------------------------------------
        # FILE_SEARCH — "find my resume", "locate the budget report", "open X"
        # Uses discovery verbs only (no content verbs like summarize/explain).
        # Returns FILE_SEARCH → orchestrator handles via metadata index (no RAG).
        # ----------------------------------------------------------------
        _FILE_DISC_VERBS = (
            r"(find|locate|search\s+for|look\s+for|open|get\s+me|show\s+me)"
        )
        _FILE_DOC_NOUNS = (
            r"(resume|cv|curriculum\s+vitae|report|invoice|contract|proposal"
            r"|notes?|manual|guide|handbook|slides?|presentation|spreadsheet"
            r"|budget|plan|schedule|log|portfolio|brochure|certificate"
            r"|deed|receipt|letter|file|document|doc)"
        )
        if not _HAS_EMAIL_KW and _re.search(
            r"\b" + _FILE_DISC_VERBS + r"\b.{0,60}\b" + _FILE_DOC_NOUNS + r"\b",
            t,
        ):
            # Exception: if query also has content verbs it's a content query
            if not _re.search(
                r"\b(summarize|summarise|what\s+does|explain|describe|tell\s+me\s+about)\b",
                t,
            ):
                return "FILE_SEARCH"

        # ----------------------------------------------------------------
        # Explicit filename with extension → FILE_SEARCH (discovery) or SUMMARY.
        # Fires BEFORE the DOCUMENT_LIST block so inputs like "find AiAgent.txt"
        # and "summarizw spring.txt" never fall through to the LLM.
        # ----------------------------------------------------------------
        _DOC_EXT_RE = (
            r"\b[\w][\w\s\-]*\.(?:pdf|docx?|pptx?|txt|md|csv|xlsx?|png|jpg|jpeg|json|log)\b"
        )
        if not _HAS_EMAIL_KW and _re.search(_DOC_EXT_RE, t, _re.IGNORECASE):
            if _re.search(r"\bsumm(?:ar|er)i[szwe]\w*\b", t, _re.IGNORECASE):
                return "SUMMARY"
            # Discovery verb + filename → FILE_SEARCH
            if _re.search(r"\b" + _FILE_DISC_VERBS + r"\b", t):
                return "FILE_SEARCH"
            return "RETRIEVAL"

        # Guard: content-query verbs (says/tells/contains/…) after a doc noun mean
        # the user wants the *contents* of a known document, not a listing.
        _CONTENT_Q_RE = (
            r"\b(?:says?|tells?|contains?|discuss(?:es)?|mentions?|means?|explains?|describes?)\b"
        )
        if not _HAS_EMAIL_KW and not _re.search(_CONTENT_Q_RE, t) and (
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
                # Summarize verb → return SUMMARY, not a listing intent
                if _re.search(r"\bsumm(?:ar|er)i[szwe]\w*\b", t, _re.IGNORECASE):
                    return "SUMMARY"
                return "DOCUMENT_LIST"

        # EMAIL_SUMMARIZE — informational questions / summary requests about the
        # email in context.  Must come BEFORE _is_email_search so that
        # "what is this email about" is not captured as EMAIL_SEARCH.
        _ECW = r"(?:this|the|that|above|it)"   # email context words
        _ENW = r"(?:mail(?:s|box)?|email(?:s|box)?|inbox|messages?)"  # email nouns
        _summarize_hit = (
            _re.search(
                r"\b(summarize|summarise|brief\s+summary|overview|gist|explain|describe)\b"
                r".{0,40}\b" + _ECW + r"\b.{0,20}\b" + _ENW + r"\b",
                t, _re.IGNORECASE,
            )
            or _re.search(
                r"\bwhat\b.{0,20}\b" + _ECW + r"\b.{0,20}\b" + _ENW
                + r"\b.{0,30}\b(about|contain|say|mean|talk|discuss)\b",
                t, _re.IGNORECASE,
            )
            or _re.search(
                r"\b" + _ECW + r"\b.{0,20}\b" + _ENW
                + r"\b.{0,30}\b(about|contain|says?|means?|talks?|discusses?)\b",
                t, _re.IGNORECASE,
            )
        )
        if _summarize_hit:
            return "EMAIL_SUMMARIZE"

        # EMAIL_QUERY — specific factual/metadata questions about the email in context.
        _email_query_hit = (
            _re.search(
                r"\b(who|when|where|which|why|how\s+(?:many|much|long|often))\b"
                r".{0,40}\b(?:this|the|that|above)\b.{0,20}\b" + _ENW + r"\b",
                t, _re.IGNORECASE,
            )
            or _re.search(
                r"\bwhat\s+(is|are|was|were)\s+(the\s+)?"
                r"(subject|sender|from|date|time|recipient|to\b|cc|attachment"
                r"|reply[\s\-]to|priority|importance)\b",
                t, _re.IGNORECASE,
            )
        )
        if _email_query_hit:
            return "EMAIL_QUERY"

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

        # ── MEMORY_RECALL ────────────────────────────────────────────────────
        # "what is my ...", "do you know my ...", "what did I tell you about my ..."
        # "what do you remember about ...", "recall my ..."
        # Checked BEFORE MEMORY_STORE so "what is my favorite X" → RECALL not STORE.
        if _re.search(
            r"\b(what\s+(?:is|are|was|were)\s+my\b"
            r"|what'?s\s+my\b"
            r"|do\s+you\s+(?:know|remember)\b"
            r"|what\s+do\s+you\s+(?:know|remember)\b"
            r"|recall\s+(?:my|what)\b"
            r"|(?:my\s+)?(?:name|language|preference|favorite|location|role)\??$)",
            t,
        ) and not _re.search(
            r"\b(email|mail|inbox|remind(?:er)?|file|document)\b", t
        ):
            return "MEMORY_RECALL"

        # ── MEMORY_STORE ─────────────────────────────────────────────────────
        # "remember that ...", "store that ...", "save that my X is Y",
        # "note that ...", "keep in mind that ...", "my favorite X is Y"
        # Guard: explicit question form ("what is my X") is already caught above.
        if _re.search(
            r"\b(remember\s+(?:that\s+)?|store\s+(?:that\s+)?|save\s+(?:that\s+)?"
            r"|note\s+(?:that\s+)?|keep\s+in\s+mind\s+(?:that\s+)?"
            r"|my\s+(?:favorite|favourite|preferred|fav)\b"
            r"|i\s+(?:prefer|like|love|use|am|work)\b)",
            t,
        ) and not _re.search(
            r"^(?:what|who|when|where|why|how|which|do\s+you)\b", t
        ) and not _re.search(
            r"\b(email|mail|inbox|remind(?:er)?|alarm)\b", t
        ):
            return "MEMORY_STORE"

        # ── INVALID_INPUT ────────────────────────────────────────────────────
        # Pure gibberish: no real words, mostly non-alpha, or <3 chars
        _stripped = _re.sub(r"[^a-z]", "", t)
        if len(_stripped) < 3:
            return "INVALID_INPUT"
        # Single token (no spaces) — check for keyboard-mash characteristics
        if " " not in t and len(t) >= 4:
            _vowels = len(_re.findall(r"[aeiou]", t))
            _consonants = len(_re.findall(r"[bcdfghjklmnpqrstvwxyz]", t))
            total = _vowels + _consonants
            # Keyboard mash: vowel ratio < 15% in a long-ish token (e.g. "asdfghjkl")
            if total >= 5 and _vowels / total < 0.15:
                return "INVALID_INPUT"
            # Also catch pure consonant strings
            if _vowels == 0:
                return "INVALID_INPUT"

        # ── Strong GENERAL escape ─────────────────────────────────────────────
        # Clearly knowledge / definition / explanation queries with no file or email
        # signal: "explain what X is", "what is deep learning", "how does X work"
        _has_file_signal = bool(_re.search(
            r"\b(file|document|doc|folder|pdf|resume|report|invoice|contract"
            r"|open|find|locate|read|load)\b", t
        ))
        _has_email_signal = bool(_re.search(
            r"\b(email|mail|inbox|reply|send|draft|compose)\b", t
        ))
        if not _has_file_signal and not _has_email_signal:
            # WH-question + concept → clear general knowledge query
            if _re.search(
                r"^(what|explain|how|why|when|who|define|describe|tell\s+me\s+about)\b"
                r".{10,}",
                t,
            ) and _re.search(
                r"\b(is|are|was|were|does|do|did|it|mean|means|works?|explain|"
                r"concept|idea|theory|algorithm|method|approach|process|system)\b",
                t,
            ):
                return "GENERAL"
        return None

    @staticmethod
    def _parse_json_intent_with_confidence(raw: str) -> tuple[Optional[str], int]:
        """Parse strict JSON intent and confidence from LLM response.
        
        Returns (intent, confidence).
        """
        import json as _json
        import re
        if not raw:
            return None, 0
            
        try:
            # Step 1: strip markdown code fences
            cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
            
            # Step 2: extract innermost { ... } object if it exists
            match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if match:
                data = _json.loads(match.group(1))
                intent = data.get("intent")
                conf = data.get("confidence", 0)
                # Handle 0.95 vs 95
                if isinstance(conf, float) and conf <= 1.0:
                    conf = int(conf * 100)
                elif isinstance(conf, (int, float)):
                    conf = int(conf)
                else:
                    conf = 0
                return intent, conf
        except Exception:
            pass
        return None, 0

# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
intent_classifier = IntentClassifier()


# ---------------------------------------------------------------------------
# Module-level detect_intent helper (hard-decision, no LLM)
# ---------------------------------------------------------------------------

def detect_intent(query: str) -> str:
    """Deterministic intent detection — FILE_SEARCH, FILE_QA, or GENERAL.

    Runs pre-LLM rules only.  Designed as a hard override gate: call this
    BEFORE any selected-file follow-up logic so that explicit search
    requests always escape to FILE_SEARCH regardless of session state.

    Returns
    -------
    "FILE_SEARCH"
        Query is searching FOR a file by name, topic, or keywords.
    "FILE_QA"
        Query wants to read / ask about content of an already-known file.
    "GENERAL"
        Neither of the above — let the normal pipeline decide.

    Examples
    --------
    detect_intent("find document related to UTV user guidance")  → "FILE_SEARCH"
    detect_intent("any doc about UTV")                           → "FILE_SEARCH"
    detect_intent("documentation for UTV app")                   → "FILE_SEARCH"
    detect_intent("show app flow document")                       → "FILE_SEARCH"
    detect_intent("what does this file say about installation")  → "FILE_QA"
    detect_intent("explain this")                                → "FILE_QA"
    """
    q = query.lower().strip()

    # ── Delegate to classify's strong doc-intent detector first ──────────────
    # This covers patterns like "show app flow document" via _FILE_DISCOVERY_RE.
    _cls_result = intent_classifier.detect_document_intent(query)
    if _cls_result in ("FILE_SEARCH", "FILE_LIST"):
        return "FILE_SEARCH"

    # ── FILE_SEARCH signals (phrase-based, fast) ─────────────────────────────
    _in_file_excl = re.compile(
        r"\b(?:in\s+(?:this|that|the|my)\s+(?:file|document|doc)"
        r"|inside\s+(?:this|that|it)"
        r"|within\s+(?:this|that|it)"
        r"|in\s+it\b|in\s+the\s+file\b)",
        re.IGNORECASE,
    )
    _search_phrases = [
        "find", "search", "look for", "locate",
        "show documents", "show document", "show docs",
        "show files", "list files", "list documents",
        "any file", "any doc", "any document",
        "documents related", "docs related", "files related",
        "documentation for", "documentation about", "documentation on",
        "document about", "doc about", "docs about",
        "document related", "doc related",
        "document on ", "files on ",
        "related to", "covering",
    ]
    if any(phrase in q for phrase in _search_phrases):
        if not _in_file_excl.search(query):
            return "FILE_SEARCH"

    # ── FILE_QA signals ──────────────────────────────────────────────────────
    _qa_phrases = [
        "what", "why", "explain", "summary", "summarize", "summarise",
        "details", "how does", "how do", "tell me about",
        "describe", "overview", "gist", "what does it say",
        "what is in", "what's in",
    ]
    if any(phrase in q for phrase in _qa_phrases):
        # Guard: pure general-knowledge questions must NOT become FILE_QA.
        # "explain machine learning", "what is AI", "how does TCP work" etc.
        # are general queries — only route to FILE_QA when a file/doc reference
        # is also present.
        _gk_pattern = re.match(
            r"^(?:what\s+(?:is|are|was|were)\b|explain\s+|define\s+"
            r"|how\s+(?:does|do|is|are)\s+|why\s+(?:is|are|does|do)\s+"
            r"|tell\s+me\s+about\s+|describe\s+)",
            q, re.IGNORECASE,
        )
        if _gk_pattern:
            _has_file_ref = re.search(
                r"\b(?:file|document|doc|resume|cv|report|folder"
                r"|attachment|uploaded|in\s+(?:this|the|my)\s+(?:file|doc))\b"
                r"|\.(?:pdf|docx?|txt|pptx?|xlsx?|csv|md|json|log)\b",
                q, re.IGNORECASE,
            )
            if not _has_file_ref:
                return "GENERAL"
        return "FILE_QA"

    return "GENERAL"
