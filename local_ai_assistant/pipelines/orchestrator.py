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

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from core.logging_config import get_logger
from configs.settings import settings
from configs.llm_config import MODEL

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


def _get_learning_service():
    try:
        from services.learning_service import learning_service
        return learning_service
    except Exception as exc:
        log.debug("LearningService unavailable: %s", exc)
        return None


def _get_planner():
    try:
        from agents.core.planner_agent import planner_agent
        return planner_agent
    except Exception as exc:
        log.debug("PlannerAgent unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Knowledge Graph v2 — lazy singletons
# ---------------------------------------------------------------------------

def _kg_extract_triples(text: str) -> list:
    """Call KG triple extractor; returns [] on any error."""
    try:
        from knowledge_graph.v2 import extract_triples
        return extract_triples(text)
    except Exception as exc:
        log.debug("[KG] extract_triples failed: %s", exc)
        return []


def _kg_get_context(query: str) -> str:
    """Call KG context builder; returns '' on any error or empty graph."""
    try:
        from knowledge_graph.v2 import get_context_for_llm
        return get_context_for_llm(query)
    except Exception as exc:
        log.debug("[KG] get_context_for_llm failed: %s", exc)
        return ""


def _is_graph_query(user_input: str) -> bool:
    """Check if query is relevant to the graph and not a tool bypass."""
    input_lower = user_input.lower()
    
    # 1. Allow Tool Fallback (bypass graph)
    tool_keywords = ["find", "search", "open", "document", "file", "email"]
    # Check whole word match for tools to avoid accidental triggers
    if any(re.search(rf"\b{k}\b", input_lower) for k in tool_keywords):
        return False
        
    try:
        from knowledge_graph.v2.graph_store import graph_store
        from knowledge_graph.v2.entities import normalize_entity
        import difflib
        
        entities = graph_store.all_entities()
        if not entities:
            return False
            
        query_norm = normalize_entity(user_input)
        existing_ids = [e.id for e in entities]
        
        # 2. Check substring matching
        for entity in entities:
            if entity.name.lower() in input_lower or entity.id in query_norm:
                return True
                
        # 3. Check fuzzy matching on words
        words = input_lower.split()
        for word in words:
            word_norm = normalize_entity(word)
            if len(word_norm) < 3:
                continue
            if difflib.get_close_matches(word_norm, existing_ids, n=1, cutoff=0.8):
                return True
                
        return False
    except Exception as exc:
        log.debug("[KG] _is_graph_query failed: %s", exc)
        return False


_EMAIL_DOMAIN_RE = re.compile(
    r"\b(email|mail|inbox|gmail|outlook|message|subject|sender|recipient)\b",
    re.IGNORECASE,
)

_PURE_FOLDER_SELECT_RE = re.compile(
    r"^\s*(?:use|set|switch\s+to|select|choose|change\s+to|go\s+to"
    r"|focus\s+on|work\s+(?:from|with|on)|apply|activate|open)\s+"
    r"(?:folder|directory|path|location)?\s*",
    re.IGNORECASE,
)

_CONTENT_VERB_RE = re.compile(
    r"\b(?:list|show|display|find|search|summarize|summarise|explain"
    r"|describe|read|scan|fetch|index|query|get|analyze|analyse)\b",
    re.IGNORECASE,
)

# Regex: queries that explicitly reference a local file/document action.
# File search / RAG must ONLY fire when one of these keywords is present.
_EXPLICIT_FILE_VERB_RE = re.compile(
    r"\b(open|read|show|summarize|summarise|find\s+file|search\s+file"
    r"|look\s+in|look\s+up|retrieve|pdf|csv|docx?|txt|report|document|internship)\b",
    re.IGNORECASE,
)


def _is_personal_fact_query(text: str) -> bool:
    """Return True when query contains NO explicit file-action verb.

    Used to stop pure knowledge-graph queries ("What is Sandeep working on?")
    from falling into the strict-file-mode or FILE_DISAMBIG path.
    """
    return not bool(_EXPLICIT_FILE_VERB_RE.search(text))


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
# File-search detection helpers  (module-level, compiled once)
# ---------------------------------------------------------------------------

# Explicit file-list / file-search verb phrases — always treat as new search.
_FILE_SEARCH_SIGNAL_RE = re.compile(
    r'\b(find\s+files?|search\s+files?|list\s+(?:all\s+)?files?'
    r'|show\s+(?:all\s+)?files?|list\s+all\b|find\s+all\b'
    r'|show\s+me\s+files?|get\s+(?:me\s+)?(?:all\s+)?files?'
    # Natural NL patterns: "find/search documents/docs related to|about|on|for"
    r'|find\s+(?:a\s+)?(?:document|doc|file)s?\b'
    r'|search\s+(?:for\s+)?(?:document|doc|file)s?\b'
    r'|look\s+for\s+(?:document|doc|file)s?\b'
    r'|any\s+(?:document|doc|file)s?\s+(?:about|on|for|related)\b'
    r'|(?:document|doc|file)s?\s+related\s+to\b'
    r'|(?:document|doc|file)s?\s+about\b'
    r'|documentation\s+(?:for|about|on)\b'
    # "show <topic> document" — verb at start, doc noun at end
    r'|show\s+(?!(?:all\s+)?(?:files?|documents?))\S+(?:\s+\S+){0,5}\s+(?:document|doc)s?\b'
    r')\b',
    re.IGNORECASE,
)

# Search verbs that can open a new file-search context when used alone.
_NEW_SEARCH_VERB_RE = re.compile(
    r'^(?:find|search(?:\s+for)?|look\s+for|locate|get\s+me|open|show\s+me)\s+',
    re.IGNORECASE,
)

# Qualifiers that anchor a search verb to the CURRENTLY selected file.
# e.g. "find the email in this file" → FILE_QA, not FILE_SEARCH.
_IN_FILE_QUALIFIER_RE = re.compile(
    r'\b(?:in\s+(?:the|this|that|my)\s+(?:file|document|doc|report)'
    r'|inside\s+(?:the|this|that|it)'
    r'|within\s+(?:the|this|that|it)'
    r'|from\s+(?:the|this|that)\s+(?:file|document|doc)'
    r'|in\s+it\b|in\s+the\s+file\b|in\s+this\b|of\s+this\s+file\b)\b',
    re.IGNORECASE,
)

# Document/file-type nouns — when a search verb targets one of these the
# query is almost certainly searching FOR a file, not asking about its content.
_FILE_TYPE_NOUN_RE = re.compile(
    r'\b(resume|cv|curriculum\s+vitae|report|invoice|budget|contract'
    r'|proposal|presentation|slides?|spreadsheet|workbook|notebook'
    r'|receipt|certificate|form|template|draft|backup|config|configuration'
    r'|log(?:s|file)?|transcript|minutes|agenda|summary|specification'
    r'|manual|guide|readme|changelog|requirements?'
    r'|documents?|docs?'  # generic document nouns — covers "find document related to ..."
    r'|pdf|docx?|xlsx?|pptx?|txt|csv|json|xml|html?|py|js|ts)\b',
    re.IGNORECASE,
)


# FIX 3: email-domain guard — queries containing email keywords must never be
# treated as file searches, even when they start with "find" / "search".
_EMAIL_QUERY_GUARD_RE = re.compile(
    r"\b(email|mail|inbox|gmail|outlook|message|subject|sender|recipient)\b",
    re.IGNORECASE,
)


def _is_new_file_search(query: str, selected_fname_stem: str = "") -> bool:
    """Return True when *query* should trigger a FILE_SEARCH even if a file
    is currently selected.

    Decision tree
    -------------
    0. Email-domain keyword present → False (never a file search)
    1. Explicit file-list pattern ("find files", "list all files") → True
    2. Search verb at start of query AND an in-file qualifier is present → False
    3. Search verb at start of query AND a file-type noun is the target → True
    4. Search verb at start of query AND the query does NOT reference the
       selected file by name AND no in-file qualifier → True
    5. Otherwise → False (treat as a follow-up question about the selected file)
    """
    # Rule 0 — email queries are never file searches
    if _EMAIL_QUERY_GUARD_RE.search(query):
        return False

    # Rule 1 — explicit file-list phrase
    if _FILE_SEARCH_SIGNAL_RE.search(query):
        return True

    # Check for leading search verb
    if not _NEW_SEARCH_VERB_RE.match(query):
        return False

    # Rule 2 — verb present but query is clearly about the current file
    if _IN_FILE_QUALIFIER_RE.search(query):
        return False

    # Rule 3 — verb + file-type noun → looking for a (different) file
    if _FILE_TYPE_NOUN_RE.search(query):
        return True

    # Rule 4 — search verb is present, no qualifier.
    # When a file IS already selected: only escape to new-search if a
    # file-type noun appears (already handled by Rule 3 above). Any other
    # target ("main points", "the author", "errors") is a follow-up on the
    # current file, not a request for a different one.
    if selected_fname_stem:
        return False

    # No selected file: bare search verb without qualifier → new search.
    return True


# ---------------------------------------------------------------------------
# File-context follow-up detection  (used to suppress GENERAL_KNOWLEDGE
# early-return when a file is already selected)
# ---------------------------------------------------------------------------

# Patterns that indicate the user is asking about the *currently selected*
# document rather than posing a general-knowledge question.
_FILE_CONTEXT_FOLLOWUP_RE = re.compile(
    r"""(?x)
    # Pronoun-based — "summarize it", "what does it say", "explain it"
      \b(?:summarize|summarise|describe|explain)\s+(?:it|that|this)\b
    | \bwhat\s+(?:is|are|does|did|was|were)\s+it\b
    | \btell\s+me\s+(?:more|about\s+it)\b
    | \bgive\s+me\s+(?:a\s+)?(?:summary|overview)\b
    | \bwhat\s+(?:is|are)\s+in\s+it\b
    # Concrete document-property questions — noun is a document attribute,
    # NOT a technical/abstract concept, so the question makes no sense
    # without a specific document in scope.
    | \bwhat\s+(?:is|are)\s+(?:the\s+)?
        (?:name|title|date|author|subject|purpose
          |version|price|amount|total|number|year|result|value
          |description|key\s+points?|main\s+points?|conclusion
          |outcome|findings?|details?|summary|overview|content
          |chapter|section|topic|heading|paragraph)\b
    # Anaphor phrases not already caught by _ANAPHORA_RE
    | \bthe\s+above\b
    | \babove\s+(?:file|document|doc)\b
    | \bthat\s+document\b
    | \bthis\s+(?:report|doc)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_file_context_followup(query: str) -> bool:
    """Return True when *query* is a vague pronoun/property follow-up that
    should be answered in the context of the currently-selected file rather
    than being treated as a general-knowledge question.

    Catches, e.g.:
    • "summarize it", "what does it say", "explain it"
    • "what is the name", "what is the date", "what are the findings"
    • "tell me more", "give me a summary"
    • "the above file", "that document"
    """
    return bool(_FILE_CONTEXT_FOLLOWUP_RE.search(query.strip()))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Routes user requests to the correct agent handler."""

    # Short one-word conversational inputs never need doc lookup
    # NOTE: "yes", "no", "ok" etc. are intentionally excluded here so the
    # permission-request handler (step 0a) can intercept them first.
    _CONVERSATIONAL: frozenset[str] = frozenset({
        "thanks", "thank you", "alright", "bye", "got it", "cool", "nice",
    })

    def __init__(self) -> None:
        self._last_email_fetch: float = 0.0
        self._watcher_started: bool = False
        self._active_file_path: Optional[str] = None
        self._active_file_content: Optional[str] = None
        self._last_response: Optional[str] = None

    def startup(self) -> None:
        """Start background services (file watcher, metadata indexer).

        Call once at application startup — safe to call multiple times.
        """
        if self._watcher_started:
            return
        self._watcher_started = True
        try:
            from services.file_watcher_service import file_watcher
            file_watcher.start()
            log.info("[ORCHESTRATOR] File watcher started")
        except Exception as exc:
            log.debug("[ORCHESTRATOR] File watcher start failed: %s", exc)

    def _is_file_related(self, query: str, active_file_content: str) -> bool:
        """Check if the query topic matches the file topic.
        
        PART 3: SIMPLIFY ROUTING LOGIC - No LLM before routing.
        """
        query_lower = query.lower()
        
        # High relevance follow-ups
        follow_ups = [
            "summarize", "what does it say", "what kind of", "explain it", 
            "tell me about", "what is it", "details", "feedback", "content",
            "what is in this file", "explain above", "this file", "the file", "it"
        ]
        if any(phrase in query_lower for phrase in follow_ups):
            return True
            
        # Default to False to allow other queries if file not explicitly referenced
        return False

    # ── main entry point ────────────────────────────────────────────────────

    def run(self, user_input: str) -> AgentResponse:
        """Main entry point for processing user requests."""
        # PART 7: TIMEOUT CONTROL — Set max execution time per task (60s)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._run_internal, user_input)
            try:
                return future.result(timeout=60.0)
            except concurrent.futures.TimeoutError:
                log.error("Orchestrator: Task timed out (60s) for: %r", user_input[:60])
                return AgentResponse(
                    answer="⚠️ Execution timed out. The task was cancelled gracefully to protect system resources.",
                    intent="TIMEOUT"
                )
            except Exception as e:
                import traceback
                log.error("Orchestrator unhandled error: %s\n%s", e, traceback.format_exc())
                return AgentResponse(answer="Something went wrong while processing your request. Please try again.", intent="ERROR")

    def process_query(self, user_input: str) -> AgentResponse:
        """Alias for run() to maintain compatibility with legacy callers."""
        log.debug("[ORCHESTRATOR] process_query called (mapping to run())")
        return self.run(user_input)

    def _run_internal(self, user_input: str) -> AgentResponse:  # noqa: C901
        """Process *user_input* and return a structured ``AgentResponse``.

        Processing priority (each stage gates the next):
        1. Access control  — BLOCK / CLARIFY / ALLOW_FOLDER / REQUEST_PERMISSION (step 2.5)
        2. Folder logic    — resolve / restrict retrieval to folder (step 5)
        3. File validation — file-folder security in retrieval Rules 1 / 1b
        4. Retrieval       — vector search with folder / file filter (Rule 2)
        5. LLM fallback    — general-agent when no document match
        """
        t0 = time.perf_counter()

        text = user_input.strip()
        if not text:
            return AgentResponse(answer="", intent="EMPTY")
            
            
        from agents.core.general_agent import normalize_query
        text = normalize_query(text)

        # PART 6: SYSTEM STATUS COMMAND
        if text.lower().strip() == "system status":
            from core.health_check import run_health_check
            from configs.settings import settings
            
            # Simple check for email
            email_status = "Connected"
            try:
                from agents.tasks.email_agent import EmailAgent
                _ea = EmailAgent()
                if not _ea.enabled: email_status = "Disabled (Missing Config)"
            except Exception:
                email_status = "Disconnected"

            status_msg = (
                f"### System Status\n"
                f"- **Indexing**: Running (Background Scan cached)\n"
                f"- **Email**: {email_status}\n"
                f"- **Model**: Loaded ({getattr(settings, 'model_name', 'Default')})\n"
                f"- **Data Path**: `{os.path.expanduser('~')}\\.ai_agent`"
            )
            return AgentResponse(answer=status_msg, intent="SYSTEM_STATUS")

        # ── 0a. Permission-request response detection ─────────────────────────
        # Must run BEFORE intent classification and every other pipeline stage
        # so that "yes" / "no" can NEVER reach the intent classifier.
        #
        # handle_response() is atomic (single lock) — no multi-step race and no
        # broad except that could silently swallow the intercept.
        from core.permission_store import permission_store as _perm_store
        _perm_action, _perm_folder, _perm_orig_query = _perm_store.handle_response(text)

        if _perm_action == "GRANT":
            # Clear any CLARIFY pending_query so it can’t interfere with the re-run
            _mem_clr = _get_memory()
            if _mem_clr is not None:
                try:
                    _mem_clr.clear_pending_query()
                    # Persist granted folder before re-run so retrieval
                    # scopes correctly even when path extraction misses a typo.
                    _mem_clr.set_last_folder(_perm_folder)
                    log.info("[ORCHESTRATOR] GRANT: last_folder set to %r", _perm_folder)
                except Exception:
                    pass
            # Index the newly-granted folder so its documents become searchable
            _index_msg = ""
            try:
                from services.document_indexer_service import document_indexer_service as _dis
                log.info("Indexing newly-granted folder: %s", _perm_folder)
                _indexed = _dis.index_folder(_perm_folder, wait=True, timeout=120.0)
                if not _indexed:
                    _index_msg = (
                        "\n\n⚠️ No documents were found in that folder "
                        "(it may be empty or contain unsupported file types)."
                    )
            except Exception as _ie:
                log.warning("index_folder failed for %s: %s", _perm_folder, _ie)
            # Register all files from the newly-granted folder in the session
            # file index so subsequent queries by filename work without a path.
            try:
                _fi_reg_mem = _get_memory()
                if _fi_reg_mem is not None:
                    _reg_n = _fi_reg_mem.register_folder_files(_perm_folder)
                    log.info(
                        "[ORCHESTRATOR] GRANT: file index +%d files from %r",
                        _reg_n, _perm_folder,
                    )
            except Exception as _fi_exc:
                log.debug("File index registration (GRANT) failed: %s", _fi_exc)
            _grant_msg = (
                f"✅ Access granted.\n\n"
                f"You can now query files from:\n"
                f"📁 {_perm_folder}\n\n"
                f"Continuing your request…{_index_msg}"
            )
            _rerun_resp = self.run(_perm_orig_query)
            _rerun_resp.answer = _grant_msg + "\n\n" + (_rerun_resp.answer or "")
            _rerun_resp.intent = "PERMISSION_GRANTED"
            _rerun_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            return _rerun_resp

        if _perm_action == "DENY":
            _deny_resp = AgentResponse(
                answer="Access request denied. I will not use that folder.",
                intent="PERMISSION_DENIED",
            )
            _deny_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            return _deny_resp

        if _perm_action == "EXPIRED":
            _exp_resp = AgentResponse(
                answer=(
                    "⏰ The previous permission request has expired (5-minute timeout). "
                    "Please re-send your original request to start a new permission prompt."
                ),
                intent="PERMISSION_EXPIRED",
            )
            _exp_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            return _exp_resp

        if _perm_action == "NO_PENDING":
            _no_pending_resp = AgentResponse(
                answer="There is no pending permission request.",
                intent="NO_PENDING_PERMISSION",
            )
            _no_pending_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            return _no_pending_resp

        # _perm_action == "NONE" — normal text; fall through to the full pipeline

        # ── 0b. Folder-clarification response detection ────────────────────────
        # When the previous turn issued a "Which folder contains X?" CLARIFY,
        # the next user message is just a folder name (e.g. "AI_Test_Documents").
        # Detect this, resolve to a full allowed path, and restore the original
        # query so the normal pipeline runs on it with the folder already set.
        _early_mem = _get_memory()
        if _early_mem is not None:
            _pending_q = _early_mem.get_pending_query()
            if _pending_q:
                try:
                    from core.access_control import resolve_folder_shortname
                    _resolved_folder = resolve_folder_shortname(text)
                    if _resolved_folder:
                        log.info(
                            "Folder-clarification reply: '%s' → '%s'; "
                            "restoring pending query: %.60s",
                            text, _resolved_folder, _pending_q,
                        )
                        _early_mem.clear_pending_query()
                        _early_mem.set_last_folder(_resolved_folder)
                        text = _pending_q  # restore original query
                    else:
                        # User moved on with a new query — discard stale pending
                        _early_mem.clear_pending_query()
                except Exception as _pq_exc:
                    log.debug("Pending-query restore failed: %s", _pq_exc)

        # ── 0c. Pending file-selection response detection ────────────────────
        # When the previous turn displayed a numbered file list and is waiting
        # for the user to pick one, intercept the reply BEFORE intent
        # classification so "1", "second one", "resume.pdf" etc. are handled
        # correctly without going through the LLM.
        _fs_mem = _get_memory()
        if _fs_mem is not None and _fs_mem.has_pending_file_selection():
            _pending_files = _fs_mem.get_pending_file_selection()
            _orig_file_query = _fs_mem.get_pending_file_query()

            # FIX 1: If the new query is clearly email-domain, discard the stale
            # FILE_DISAMBIG state immediately so the email pipeline runs unobstructed.
            if _EMAIL_DOMAIN_RE.search(text):
                log.info(
                    "[ORCHESTRATOR] Pending FILE_DISAMBIG cleared — email query detected: %r",
                    text[:60],
                )
                _fs_mem.clear_pending_file_selection()
            else:
                try:
                    from services.file_search_service import file_search_service as _fss
                    _sel_path = _fss.resolve_selection(text, _pending_files)
                except Exception as _fse:
                    log.debug("[ORCHESTRATOR] file selection resolve failed: %s", _fse)
                    _sel_path = None

                if _sel_path is not None:
                    # Valid selection — clear state and process the chosen file
                    _fs_mem.clear_pending_file_selection()
                    log.info(
                        "[ORCHESTRATOR] File selected: %r (from %d candidates)",
                        _sel_path, len(_pending_files),
                    )
                    _sel_resp = self._handle_selected_file(
                        _sel_path, _orig_file_query or text
                    )
                    _sel_resp.intent = "FILE_SELECT"
                    _sel_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _sel_resp, tool_name="FILE_SELECT")
                    return _sel_resp
                else:
                    # Input doesn't resolve to a file — user moved on; clear state
                    # and let the normal pipeline handle it.
                    log.info(
                        "[ORCHESTRATOR] Pending file selection not resolved for %r — clearing",
                        text[:60],
                    )
                    _fs_mem.clear_pending_file_selection()
        # ── 1. Conversati        # ═══════════════════════════════════════════════════════════════════
        # PART 1: ROUTING & CONTEXT RESOLUTION
        # ═══════════════════════════════════════════════════════════════════
        from agents.core.planner_agent import get_semantic_mode
        
        semantic_mode = get_semantic_mode(text, has_active_file=bool(self._active_file_path))
        query_lower = text.lower()
        
        if semantic_mode == "EMAIL_SEARCH":
            print("[EMAIL] Search Feature triggered")
            try:
                from agents.tasks.email_agent import EmailAgent
                agent = EmailAgent()
                results = agent.search_live_imap(query_lower)
                
                # PART 1: STORE RESULTS
                self.last_email_results = results
                
                # PART 2: VALIDATE STORAGE
                print("[EMAIL] Stored results:", len(getattr(self, "last_email_results", [])))
                
                if not results:
                    ans = f"No matching emails found for: {text}"
                else:
                    ans = f"Found {len(results)} email(s) for: {text}\n\n"
                    for mail in results[:10]:
                        ans += f"- [{mail.get('id', 'N/A')}] From: {mail.get('from', 'Unknown')} | Subject: {mail.get('subject', '')}\n"
                        if mail.get('snippet'): ans += f"  {mail.get('snippet')}\n"
                        
                resp = AgentResponse(answer=ans.strip(), intent="EMAIL_SEARCH")
            except Exception as e:
                import traceback
                print(traceback.format_exc())
                print(f"[EMAIL ERROR] {e}")
                resp = AgentResponse(answer=f"Email search error: {e}", intent="EMAIL_SEARCH_ERROR")
                
            resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, resp, tool_name="EMAIL_SEARCH")
            return resp
            
        elif semantic_mode == "EMAIL_REPLY":
            print("[EMAIL] Reply Feature triggered")
            try:
                from agents.knowledge.email_reply_agent_v2 import generate_email_reply
                last_results = getattr(self, "last_email_results", [])
                
                # PART 3: USE IN REPLY
                if not last_results:
                    ans = "No email context available. Please search for an email first."
                    resp = AgentResponse(answer=ans, intent="EMAIL_REPLY")
                    resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, resp, tool_name="EMAIL_REPLY")
                    return resp
                
                # PART 4: SELECT CORRECT EMAIL
                selected_email = None
                exact_matches = []
                partial_matches = []
                
                words = [w for w in query_lower.split() if len(w) > 3 and w not in ('reply', 'mail', 'email', 'that', 'this', 'above', 'with')]
                

                for email in last_results:
                    subject = email.get("subject", "").lower()
                    snippet = email.get("snippet", "").lower()
                    
                    matched = False
                    for w in words:
                        # Exact match: word boundary
                        if re.search(rf"\b{re.escape(w)}\b", subject) or re.search(rf"\b{re.escape(w)}\b", snippet):
                            exact_matches.append(email)
                            matched = True
                            break
                    
                    if not matched:
                        for w in words:
                            if w in subject or w in snippet:
                                partial_matches.append(email)
                                break
                                
                # Priority match: exact > partial > fallback (most recent is at the end of the list)
                if exact_matches:
                    selected_email = exact_matches[-1]
                elif partial_matches:
                    selected_email = partial_matches[-1]
                else:
                    selected_email = last_results[-1]
                
                # Generate reply
                draft = generate_email_reply(selected_email, user_content=text)
                if draft:
                    # Save to drafts
                    from agents.tasks.email_agent import EmailAgent
                    email_agent = EmailAgent()
                    to_email = selected_email.get('from', '')
                    subject = selected_email.get('subject', 'No Subject')
                    
                    success = email_agent.save_draft(to_email, subject, draft)
                    if success:
                        ans = f"Drafted and saved reply to {to_email} in Gmail Drafts:\n\n{draft}"
                    else:
                        ans = f"Drafted reply to {to_email}, but failed to save to Drafts:\n\n{draft}"
                else:
                    ans = "Failed to generate reply draft."
                    
                resp = AgentResponse(answer=ans, intent="EMAIL_REPLY")
            except Exception as e:
                import traceback
                print(traceback.format_exc())
                resp = AgentResponse(answer=f"Email reply error: {e}", intent="EMAIL_REPLY_ERROR")
                
            resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, resp, tool_name="EMAIL_REPLY")
            return resp
            
        elif semantic_mode == "EMAIL_COMPOSE":
            print("[EMAIL] Compose Feature triggered")
            ans = "Email composition feature is available via standard text, but direct sending is not yet configured."
            resp = AgentResponse(answer=ans, intent="EMAIL_COMPOSE")
            resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, resp, tool_name="EMAIL_COMPOSE")
            return resp
            
        elif semantic_mode == "FILE_SEARCH":
            if self._active_file_path:
                print("[ROUTER] New file search overrides active file")
                self._active_file_path = None
                self._active_file_content = None
                
            print("[ROUTER] Explicit file search detected")
            _fs_resp = self._handle_file_search(text)
            _fs_resp.intent = "FILE_SEARCH"
            
            # If search resolves to a single file, set it as active
            _mem = _get_memory()
            if _mem:
                sel_file = _mem.get_selected_file()
                if sel_file and os.path.isfile(sel_file):
                    if sel_file != self._active_file_path:
                        print(f"[FILE] Selected: {os.path.basename(sel_file)}")
                        self._active_file_path = sel_file
                        self._active_file_content = None # Force reload on next turn
            
            _fs_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, _fs_resp, tool_name="FILE_SEARCH")
            return _fs_resp

        elif semantic_mode == "FILE_QA":
            # Safety check: if no active file, we can't do FILE_QA. Fall back to GENERAL.
            if not self._active_file_path:
                print("[ROUTER] FILE_QA triggered but no active file. Routing to GENERAL")
                semantic_mode = "GENERAL"
            else:
                print("[ROUTER] FILE_QA triggered")
                print(f"[FILE] Active context: {os.path.basename(self._active_file_path)}")
                try:
                    # Reload content if missing
                    if not self._active_file_content:
                        from agents.knowledge.retrieval_agent import _load_document_from_path
                        loaded_content = _load_document_from_path(self._active_file_path)
                        self._active_file_content = loaded_content if loaded_content else ""

                    # PART 7: VALIDATE INPUT BEFORE LLM
                    assert len(self._active_file_content) > 50, "File content not properly loaded."
                    print("[FILE] Content length:", len(self._active_file_content))

                    from agents.knowledge.retrieval_agent import answer_from_file
                    ans = answer_from_file(text, self._active_file_content, model_name=MODEL, file_path_used=self._active_file_path)
                    resp = AgentResponse(answer=ans, intent="FILE_QA")
                    resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, resp, tool_name="FILE_QA")
                    return resp
                except AssertionError as e:
                    resp = AgentResponse(answer=str(e), intent="FILE_QA_ERROR")
                    resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    return resp
                except Exception as e:
                    import traceback
                    print(traceback.format_exc())
                    log.error(f"[FILE] Error in Active File QA: {e}")
                    resp = AgentResponse(answer="Error processing file. Please try again.", intent="FILE_QA_ERROR")
                    resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    return resp

        if semantic_mode == "GENERAL":
            # 3. PRIORITY: Conversational Context ("above", "previous", "that")
            # PART 6: Disable memory override if active_file exists
            if self._last_response and not self._active_file_path and re.search(r"\b(above|previous|that|it|response|answer)\b", query_lower):
                print("[ROUTER] Using last response memory")
                
                # Combine previous response with current query as context
                contextual_query = f"Previous Response: {self._last_response}\n\nUser Question: {text}"
                
                _gk_resp = self._handle_general_ai_response(contextual_query)
                _gk_resp.intent = "GENERAL"
                _gk_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                self._post_process(text, _gk_resp)
                return _gk_resp

            print("[ROUTER] Routing to GENERAL")
            _gk_resp = self._handle_general_ai_response(text, memory=_get_memory())
            _gk_resp.intent = "GENERAL"
            _gk_resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, _gk_resp)
            return _gk_resp

        print(f"[ROUTER] Tool mode detected ({semantic_mode}) — falling through to classifier")
        # ── 2.7. Strict file mode ─────────────────────────────────────────────

        # ── 2.7. Strict file mode ─────────────────────────────────────────────
        # Handles three scenarios in priority order:
        #
        #   A) Anaphora — "open it" / "summarize that file" / "the same file"
        #      → resolves against memory.get_selected_file() from the last turn
        #
        #   B) Explicit filename(s) with a known extension present in *text*
        #      ├─ Two or more filenames → load each file → combined answer
        #      ├─ Exactly one filename, 1 SQLite match → set selected_file → QA
        #      ├─ Exactly one filename, 2+ matches → disambiguation list
        #      └─ Exactly one filename, 0 matches → filesystem walk → "not found"
        #
        #   C) No explicit filename → fuzzy keyword search ("my resume", "the report")
        #      ├─ 1 fuzzy match → set selected_file → QA
        #      └─ 2+ fuzzy matches → disambiguation list
        #
        # Falls through to the rest of the pipeline when none of the above fire.
        #
        # KG GUARD: bypass this entire block when the query has NO explicit
        # file-action verb.  Prevents personal-fact queries like
        # "What is Sandeep working on?" from triggering FILE_DISAMBIG because
        # a name coincidentally matches a filename in the index.
        _sfm_mem = _get_memory()
        if not _is_personal_fact_query(text):
          try:
            from agents.knowledge.retrieval_agent import (
                _extract_all_filenames_from_query as _efq_all,
                _resolve_filepath_from_index,
                _find_file_under_root,
                _fuzzy_resolve_from_index,
            )
            from core.access_control import ALLOWED_FOLDERS as _ALLOWED_FOLDERS
            from services.file_search_service import file_search_service as _fss_sfm

            # ── A) Anaphora shortcut ──────────────────────────────────────────
            _ANAPHORA_RE = re.compile(
                r"\b(open it|read it|summarize it|summarise it|show it|"
                r"open that|read that|summarize that|summarise that|"
                r"that file|same file|the file|this file|"
                r"the document|this document|that document|"
                r"document I uploaded|file I uploaded|"
                r"document I shared|file I shared)\b",
                re.IGNORECASE,
            )
            if _sfm_mem is not None and _ANAPHORA_RE.search(text):
                _sfm_remembered = _sfm_mem.get_selected_file()
                if _sfm_remembered and os.path.isfile(_sfm_remembered):
                    log.info(
                        "[ORCHESTRATOR] Anaphora resolved to %r", _sfm_remembered
                    )
                    _sfm_resp = self._handle_file_qa(_sfm_remembered, text)
                    _sfm_resp.intent = "FILE_QA"
                    _sfm_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _sfm_resp, tool_name="FILE_QA")
                    return _sfm_resp

            # ── A2) Selected-file follow-up shortcut ──────────────────────────
            # When the user has already selected/resolved a file (selected_file is
            # set) and the current query carries no NEW file signal, route directly
            # to FILE_QA.  This prevents follow-up questions ("what are the key
            # features?", "who wrote this?") from falling back into FILE_SEARCH.
            #
            # A query escapes to FILE_SEARCH when _is_new_file_search() returns
            # True, which covers:
            #   •  explicit "find files" / "list all files" verb phrases
            #   •  bare search verbs + file-type noun  ("find resume")
            #   •  bare search verbs without an in-file qualifier and without
            #      any word that overlaps the selected file's stem
            #   •  a DIFFERENT filename (with a known extension) present in query
            #
            # A query also escapes when it is clearly a general/LLM/memory query —
            # "explain deep learning", "what is an AI agent", "remember that ..."
            # must NEVER be answered from the selected file context.
            _A2_GENERAL_ESCAPE_RE = re.compile(
                r"^(?:what\s+is|what\s+are|explain|how\s+does|how\s+do|why\s+is|why\s+are"
                r"|define|describe|tell\s+me\s+about|what\s+do\s+you\s+know\s+about"
                r"|remember\s+that|note\s+that|my\s+(?:favorite|favourite|preferred)"
                r"|what\s+(?:is|are|was|were)\s+my\b|do\s+you\s+(?:know|remember)\b)\b",
                re.IGNORECASE,
            )
            if _sfm_mem is not None:
                _sfm_sel = _sfm_mem.get_selected_file()
                if _sfm_sel and os.path.isfile(_sfm_sel):
                    _sfm_sel_stem = os.path.splitext(
                        os.path.basename(_sfm_sel)
                    )[0]
                    # Check 1: module-level new-search heuristic
                    _sfm_is_new_search = _is_new_file_search(text, _sfm_sel_stem)
                    # Check 2: a DIFFERENT filename with extension in query
                    _sfm_sel_fname = os.path.basename(_sfm_sel).lower()
                    _sfm_other_files = [
                        f for f in _efq_all(text)
                        if f.lower() != _sfm_sel_fname
                    ]
                    # Check 3 (HARD OVERRIDE): the intent classifier independently
                    # identifies this as a FILE_SEARCH/FILE_LIST request.
                    # This is the critical guard that prevents "find document related
                    # to UTV" from being routed to FILE_QA on the selected file.
                    _sfm_intent_override = False
                    try:
                        from core.intent_classifier import (
                            intent_classifier as _ic_sfm_a2,
                            detect_intent as _detect_intent_a2,
                        )
                        _sfm_doc_intent = _ic_sfm_a2.detect_document_intent(text)
                        _sfm_hard_intent = _detect_intent_a2(text)
                        _sfm_intent_override = (
                            _sfm_doc_intent in ("FILE_SEARCH", "FILE_LIST")
                            or _sfm_hard_intent == "FILE_SEARCH"
                        )
                    except Exception as _sfm_ic_exc:
                        log.debug(
                            "[ORCHESTRATOR] A2 intent-override check failed: %s",
                            _sfm_ic_exc,
                        )
                    if not _sfm_is_new_search and not _sfm_other_files and not _sfm_intent_override:
                        # Final guard: if query is clearly a general/knowledge/memory
                        # question with NO reference to the selected file, escape to LLM.
                        #
                        # CRITICAL: "what are the key points in the document I uploaded?"
                        # starts with "what are" but IS a file-content question.
                        # Only escape when there is NO anaphoric file reference in
                        # the query (e.g. "in the document", "document I uploaded",
                        # "in this file", "in the report", etc.).
                        #
                        # Memory queries always escape regardless of file anaphora.
                        _sfm_is_memory_q = bool(re.match(
                            r"^(?:what\s+(?:is|are|was|were)\s+my\b"
                            r"|do\s+you\s+(?:know|remember)\b"
                            r"|remember\s+that|note\s+that"
                            r"|my\s+(?:favorite|favourite|preferred))\b",
                            text.strip(), re.IGNORECASE,
                        ))
                        _sfm_has_file_anaphora = bool(re.search(
                            r"\b(?:the\s+(?:document|file|doc|report|pdf|image|picture)"
                            r"|document\s+(?:i|we)\s+(?:uploaded|shared|sent|mentioned|provided|gave)"
                            r"|file\s+(?:i|we)\s+(?:uploaded|shared|sent|mentioned|provided|gave)"
                            r"|in\s+(?:this|the)\s+(?:file|document|doc|report)"
                            r"|from\s+(?:this|the)\s+(?:file|document|doc)"
                            r"|of\s+this\s+(?:file|document|doc))\b"
                            r"|\.(?:pdf|docx?|txt|pptx?|csv|xlsx?|md|json|log)\b",
                            text, re.IGNORECASE,
                        ))
                        _sfm_is_general_escape = (
                            _sfm_is_memory_q
                            or (
                                bool(_A2_GENERAL_ESCAPE_RE.match(text))
                                and not _sfm_has_file_anaphora
                                # Don't escape if the query is a vague pronoun/
                                # property follow-up — e.g. "what is the name",
                                # "what is the date", "what are the findings".
                                # Those must be answered from the selected file.
                                and not _is_file_context_followup(text)
                            )
                        )
                        if _sfm_is_general_escape:
                            log.info(
                                "[ORCHESTRATOR] A2 GENERAL escape: clearing selected_file "
                                "for general query=%r", text[:60],
                            )
                            _sfm_mem.set_selected_file("")
                        else:
                            log.info(
                                "[ORCHESTRATOR] Selected-file follow-up → FILE_QA: "
                                "file=%r  query=%r",
                                _sfm_sel, text[:60],
                            )
                            _sfm_resp = self._handle_file_qa(_sfm_sel, text)
                            _sfm_resp.intent = "FILE_QA"
                            _sfm_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                            self._post_process(text, _sfm_resp, tool_name="FILE_QA")
                            return _sfm_resp
                    elif _sfm_is_new_search or _sfm_other_files or _sfm_intent_override:
                        # Context switch: clear the stale selected file so the
                        # upcoming FILE_SEARCH can set a fresh one.
                        log.info(
                            "[ORCHESTRATOR] New file search detected — clearing "
                            "selected file %r  query=%r  (override=%s)",
                            _sfm_sel, text[:60], _sfm_intent_override,
                        )
                        _sfm_mem.set_selected_file("")

            # ── B) Explicit filename(s) ───────────────────────────────────────
            _sfm_filenames = _efq_all(text)

            if len(_sfm_filenames) >= 2:
                # Multi-file query: resolve each, load, answer from combined content
                _sfm_multi_paths: list[str] = []
                for _fn in _sfm_filenames[:3]:  # cap at 3 files
                    _fn_hits = _resolve_filepath_from_index(_fn)
                    _fn_path: Optional[str] = _fn_hits[0]["path"] if _fn_hits else None
                    if not _fn_path:
                        for _sfm_root in _ALLOWED_FOLDERS:
                            _fn_path = _find_file_under_root(_sfm_root, _fn)
                            if _fn_path:
                                break
                    if _fn_path and os.path.isfile(_fn_path):
                        _sfm_multi_paths.append(_fn_path)

                if len(_sfm_multi_paths) >= 2:
                    log.info(
                        "[ORCHESTRATOR] Multi-file mode: %s", _sfm_multi_paths
                    )
                    _sfm_resp = self._handle_multi_file_qa(_sfm_multi_paths, text)
                    _sfm_resp.intent = "FILE_QA"
                    _sfm_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _sfm_resp, tool_name="FILE_QA")
                    return _sfm_resp

            elif len(_sfm_filenames) == 1:
                _sfm_filename = _sfm_filenames[0]

                # a) SQLite lookup (returns list)
                _sfm_matches = _resolve_filepath_from_index(_sfm_filename)

                # b) Filesystem walk fallback for unindexed files
                if not _sfm_matches:
                    for _sfm_root in _ALLOWED_FOLDERS:
                        _p = _find_file_under_root(_sfm_root, _sfm_filename)
                        if _p:
                            _sfm_matches = [{
                                "path": _p,
                                "name": os.path.basename(_p),
                                "folder": os.path.dirname(_p),
                                "mtime": 0.0,
                            }]
                            break

                # c) Dynamically-granted folder walk
                if not _sfm_matches:
                    try:
                        from core.permission_store import permission_store as _sfm_ps
                        for _sfm_granted in _sfm_ps.get_granted_folders():
                            _p = _find_file_under_root(_sfm_granted, _sfm_filename)
                            if _p:
                                _sfm_matches = [{
                                    "path": _p,
                                    "name": os.path.basename(_p),
                                    "folder": os.path.dirname(_p),
                                    "mtime": 0.0,
                                }]
                                break
                    except Exception:
                        pass

                if len(_sfm_matches) == 1:
                    # Unambiguous single match
                    _sfm_path = _sfm_matches[0]["path"]
                    log.info(
                        "[ORCHESTRATOR] Strict file mode: %r → %r",
                        _sfm_filename, _sfm_path,
                    )
                    if _sfm_mem is not None:
                        _sfm_mem.set_selected_file(_sfm_path)
                    # Pure search queries ("find X", "locate X") → confirm
                    # selection without running QA on the search query itself.
                    _sfm_resp = self._handle_selected_file(_sfm_path, text)
                    if not _sfm_resp.intent:
                        _sfm_resp.intent = "FILE_SELECT"
                    _sfm_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _sfm_resp, tool_name=_sfm_resp.intent)
                    return _sfm_resp

                elif len(_sfm_matches) >= 2:
                    # Ambiguous: present numbered disambiguation list
                    log.info(
                        "[ORCHESTRATOR] Strict file mode: %d matches for %r",
                        len(_sfm_matches), _sfm_filename,
                    )
                    if _sfm_mem is not None:
                        _sfm_mem.set_pending_file_selection(
                            _sfm_matches, original_query=text
                        )
                    _disambig_header = (
                        f"I found {len(_sfm_matches)} files named **{_sfm_filename}**:"
                    )
                    _listing = _fss_sfm.format_listing(
                        _sfm_matches, header=_disambig_header
                    )
                    _sfm_disambig = AgentResponse(
                        answer=_listing + "\n\nWhich one would you like to use?",
                        intent="FILE_DISAMBIG",
                    )
                    _sfm_disambig.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _sfm_disambig)
                    return _sfm_disambig

                else:
                    # File not found anywhere
                    log.info(
                        "[ORCHESTRATOR] Strict file mode: %r not found in any index/root",
                        _sfm_filename,
                    )
                    _sfm_nf = AgentResponse(
                        answer=(
                            f"\u26a0\ufe0f Could not find **{_sfm_filename}** in any"
                            " indexed location.\n\n"
                            "Suggestions:\n"
                            f"- Use **\"find {_sfm_filename}\"** to search for it\n"
                            "- Use **\"list my files\"** to see all indexed files\n"
                            "- Grant access to the folder containing this file first"
                        ),
                        intent="FILE_NOT_FOUND",
                    )
                    _sfm_nf.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _sfm_nf)
                    return _sfm_nf

            else:
                # No explicit filename — try fuzzy content-query detection
                # e.g. "what does my resume say", "summarize the report"
                # Skip if this is a FILE_SEARCH/FILE_LIST query; step 2.8 handles those.
                # Also skip when the query contains email-domain keywords — fuzzy file
                # matching on "find email from John" would wrongly surface file results.
                _sfm_is_fsd = False
                try:
                    from core.intent_classifier import intent_classifier as _ic_sfm
                    _sfm_doc_intent = _ic_sfm.detect_document_intent(text)
                    if _sfm_doc_intent in ("FILE_SEARCH", "FILE_LIST"):
                        _sfm_is_fsd = True
                    # FIX 2: treat EMAIL_* intents as an explicit skip for fuzzy matching
                    elif re.search(
                        r"\b(email|mail|inbox|gmail|outlook)\b", text, re.IGNORECASE
                    ):
                        _sfm_is_fsd = True
                        log.info(
                            "[ORCHESTRATOR] Fuzzy file match suppressed — email keyword in query: %r",
                            text[:60],
                        )
                except Exception:
                    pass
                if not _sfm_is_fsd:
                    _sfm_fuzzy = _fuzzy_resolve_from_index(text)
                    if _sfm_fuzzy:
                        if len(_sfm_fuzzy) == 1:
                            _sfm_path = _sfm_fuzzy[0]["path"]
                            log.info("[ORCHESTRATOR] Fuzzy file mode: %r", _sfm_path)
                            if _sfm_mem is not None:
                                _sfm_mem.set_selected_file(_sfm_path)
                            # Route through _handle_selected_file so pure
                            # search queries get a confirmation, not QA.
                            _sfm_resp = self._handle_selected_file(_sfm_path, text)
                            if not _sfm_resp.intent:
                                _sfm_resp.intent = "FILE_SELECT"
                            _sfm_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                            self._post_process(text, _sfm_resp, tool_name=_sfm_resp.intent)
                            return _sfm_resp
                        else:
                            log.info(
                                "[ORCHESTRATOR] Fuzzy file mode: %d candidates",
                                len(_sfm_fuzzy),
                            )
                            if _sfm_mem is not None:
                                _sfm_mem.set_pending_file_selection(
                                    _sfm_fuzzy, original_query=text
                                )
                            _listing = _fss_sfm.format_listing(
                                _sfm_fuzzy[:10],
                                header="I found several matching files:",
                            )
                            _sfm_fuzzy_resp = AgentResponse(
                                answer=_listing + "\n\nWhich file did you mean?",
                                intent="FILE_DISAMBIG",
                            )
                            _sfm_fuzzy_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                            self._post_process(text, _sfm_fuzzy_resp)
                            return _sfm_fuzzy_resp
                    # Falls through to rest of pipeline when no fuzzy match

          except Exception as _sfm_exc:
              log.debug("[ORCHESTRATOR] Strict file mode pre-check failed: %s", _sfm_exc)
        else:
            log.debug(
                "[KG] Strict-file-mode SKIPPED — no explicit file verb in: %.60s", text
            )

        # ── 2.8. File-discovery fast-path — global metadata search, no folder needed
        # FILE_SEARCH and FILE_LIST queries search the SQLite index globally and must
        # never pass through the access-control CLARIFY path ("Which folder?").
        # Detect the intent here, before step 2.5, and return immediately.
        try:
            from core.intent_classifier import intent_classifier as _ic_fd
            _fd_intent = _ic_fd.detect_document_intent(text)
            if _fd_intent in ("FILE_SEARCH", "FILE_LIST"):
                log.info(
                    "[ORCHESTRATOR] File-discovery fast-path: intent=%s | %.60s",
                    _fd_intent, text,
                )
                # ── Clear stale file context ─────────────────────────────
                # A new FILE_SEARCH/FILE_LIST query must NOT be restricted
                # to the previously-viewed file.  Clear context so the
                # search runs globally.
                _ctx_mem = _get_memory()
                if _ctx_mem is not None:
                    _old_sel = _ctx_mem.get_selected_file()
                    _old_last = _ctx_mem.get_last_file()
                    if _old_sel or _old_last:
                        _ctx_mem.set_selected_file("")
                        _ctx_mem.set_last_file("")
                        _ctx_mem.clear_pending_documents()
                        print(
                            f"[DEBUG] Context cleared for FILE_SEARCH: "
                            f"selected_file={_old_sel!r} → '', "
                            f"last_file={_old_last!r} → ''"
                        )
                        log.info(
                            "[ORCHESTRATOR] Context cleared: selected_file=%r last_file=%r",
                            _old_sel, _old_last,
                        )
                _fd_resp = (
                    self._handle_file_list()
                    if _fd_intent == "FILE_LIST"
                    else self._handle_file_search(text)
                )
                _fd_resp.intent = _fd_intent
                _fd_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                self._post_process(text, _fd_resp, tool_name=_fd_intent)
                return _fd_resp
            elif _fd_intent == "SUMMARY":
                # Summarize + document context — resolve via pending_documents
                # or selected_file, then dispatch to summary handler.
                log.info("[ORCHESTRATOR] File-discovery fast-path: SUMMARY detected, resolving target file")
                _summ_mem = _get_memory()
                _summ_file = None
                if _summ_mem is not None:
                    _summ_file = _summ_mem.get_selected_file() or _summ_mem.get_last_file()
                    if not _summ_file:
                        _pdocs = _summ_mem.get_pending_documents()
                        if _pdocs and len(_pdocs) == 1:
                            _summ_file = _pdocs[0].get("path", "")
                        elif _pdocs:
                            log.info("[ORCHESTRATOR] SUMMARY: multiple pending docs, asking user to pick")
                            # Fall through to main pipeline which will disambiguate
                if _summ_file:
                    _summ_mem.set_last_file(os.path.basename(_summ_file))
                    _summ_mem.set_selected_file(_summ_file)
                    _summ_mem.set_last_folder(os.path.dirname(_summ_file))
                    # Use file Q&A handler (same as FILE_SELECT → summarize)
                    _summ_resp = self._handle_file_qa(_summ_file, text)
                    _summ_resp.intent = "SUMMARY"
                    _summ_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _summ_resp, tool_name="SUMMARY")
                    return _summ_resp
                # No file resolved — fall through to main pipeline
        except Exception as _fde:
            log.debug("[ORCHESTRATOR] File-discovery pre-check failed: %s", _fde)

        # ── 2.5. Access control — handle file/path/permission questions ───────
        # BLOCK / CLARIFY short-circuit; ALLOW_FOLDER injects folder context
        # and skips last_file enrichment in step 5.
        _skip_last_file = False
        _forced_folder: Optional[str] = None
        try:
            from core.access_control import check_access_query, AccessDecision as _ACD
            _ac_mem = _get_memory()
            _last_folder = _ac_mem.get_last_folder() if _ac_mem is not None else None
            _ac = check_access_query(text, last_folder=_last_folder)
            if _ac.action == "REQUEST_PERMISSION":
                log.debug("Access control REQUEST_PERMISSION for folder: %r", _ac.folder_path)
                # Store pending permission request so the next "yes" can re-run it
                try:
                    from core.permission_store import permission_store as _perm_store
                    _perm_store.set_pending(_ac.folder_path, text)
                except Exception as _pe:
                    log.debug("permission_store.set_pending failed: %s", _pe)
                _perm_resp = AgentResponse(answer=_ac.message, intent="REQUEST_PERMISSION")
                _perm_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                self._post_process(text, _perm_resp)
                return _perm_resp
            elif _ac.action in ("BLOCK", "CLARIFY"):
                log.debug("Access control %s for query: %.60s", _ac.action, text)
                # When asking the user which folder a file is in, save the
                # original query so the next turn can restore and rerun it.
                if _ac.action == "CLARIFY" and _ac_mem is not None:
                    try:
                        _ac_mem.set_pending_query(text)
                    except Exception:
                        pass
                _ac_resp = AgentResponse(answer=_ac.message, intent="ACCESS_CONTROL")
                _ac_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                self._post_process(text, _ac_resp)
                return _ac_resp
            elif _ac.action == "ALLOW_FOLDER":
                _skip_last_file = True
                _forced_folder = _ac.folder_path
                if _ac_mem is not None:
                    try:
                        _ac_mem.set_last_folder(_ac.folder_path)
                        # Register folder files in the index on access
                        _ac_mem.register_folder_files(_ac.folder_path)
                    except Exception:
                        pass
                log.info("[ORCHESTRATOR] Folder resolved (ALLOW_FOLDER): %r", _forced_folder)
                # ── Pure folder-selection fast-exit ───────────────────────────
                # When the user just said "Use folder X" / "Set folder X" with no
                # embedded content verb, return a confirmation immediately.
                # Falling through would classify as DOCUMENT_FOLDER_QUERY and
                # issue a document search with no real search terms.
                if _PURE_FOLDER_SELECT_RE.match(text) and not _CONTENT_VERB_RE.search(text):
                    log.info(
                        "[ORCHESTRATOR] Pure folder-selection detected — "
                        "returning confirmation for folder=%r", _forced_folder
                    )
                    print(f"[DEBUG] Selected folder: {_forced_folder}")
                    _fold_resp = AgentResponse(
                        answer=(
                            f"✅ **Folder selected:** `{_forced_folder}`\n\n"
                            f"File listing and searches are now scoped to this folder. "
                            f"Say **\"list files\"** to see what's inside."
                        ),
                        intent="FOLDER_SELECT",
                    )
                    _fold_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _fold_resp)
                    return _fold_resp
        except Exception as _ac_exc:
            log.debug("access_control check failed: %s", _ac_exc)

        # ── 3. Auto-extract user facts from input ───────────────────────────
        memory = _get_memory()
        if memory is not None:
            try:
                memory.extract_and_store(text)
            except Exception as exc:
                log.debug("memory.extract_and_store failed: %s", exc)

        # ── 3.5. Learning style hint ─────────────────────────────────────────
        _style_hint: str = ""
        _learning_svc = _get_learning_service()
        if _learning_svc is not None:
            try:
                _style_hint = _learning_svc.build_style_hint()
            except Exception as _lsh_exc:
                log.debug("build_style_hint failed: %s", _lsh_exc)

        # ── 3.6. Multi-step plan detection ──────────────────────────────────
        _active_plan = None
        _planner_svc = _get_planner()
        if _planner_svc is not None:
            try:
                _last_intent_for_plan = memory.get_last_intent() if memory is not None else None
                _last_file_for_plan = memory.get_last_file() if memory is not None else None
                _active_plan = _planner_svc.plan_with_context(
                    text,
                    last_intent=_last_intent_for_plan,
                    last_file=_last_file_for_plan,
                )
                if _active_plan and not _active_plan.is_single_step():
                    log.info("[ORCHESTRATOR] Multi-step plan detected: %s", _active_plan.as_text())
            except Exception as _plan_exc:
                log.debug("Planner failed: %s", _plan_exc)
                _active_plan = None

        # ── 4. Intent classification ─────────────────────────────────────────
        intent = self._classify_intent(text, memory)
        log.debug("Intent: %s  |  input: %.60s", intent, text)

        # ── Debug context dump ─────────────────────────────────────────────
        _dbg_sel = memory.get_selected_file() if memory is not None else None
        _dbg_last = memory.get_last_file() if memory is not None else None
        _dbg_folder = memory.get_last_folder() if memory is not None else None
        _dbg_pending = len(memory.get_pending_documents()) if memory is not None else 0
        print(
            f"[DEBUG] Intent={intent} | selected_file={_dbg_sel!r} | "
            f"last_file={_dbg_last!r} | folder={_dbg_folder!r} | "
            f"pending_docs={_dbg_pending} | query={text[:60]!r}"
        )

        # ── 4a. Intent overrides ──────────────────────────────────────────────
        # "files from/in <location>" queries need folder-scoped retrieval.
        if re.search(r"\bfiles?\s+(from|in|at|inside|under)\b", text, re.IGNORECASE) and intent in {
            "RETRIEVAL", "DOCUMENT_SEARCH", "GENERAL",
        }:
            intent = "DOCUMENT_FOLDER_QUERY"
            log.debug("Intent overridden to DOCUMENT_FOLDER_QUERY for 'files from/in' query")

        # ── 4b. Email context safety net ─────────────────────────────────────
        # Last-resort override: when a prior email is in memory and the input
        # carries any loose reply/response signal but the classifier still
        # returned a non-email intent (GENERAL/CHAT/etc.), force EMAIL_REPLY.
        # This guards against LLM misclassifications that slip through all layers.
        _EMAIL_REPLY_OVERRIDE_INTENTS = {"GENERAL", "CHAT", "GREETING", "RETRIEVAL"}
        if intent in _EMAIL_REPLY_OVERRIDE_INTENTS and memory is not None:
            try:
                _last_email_orch = memory.get_last_email()
                _search_res_orch = memory.get_last_email_search_results() if not _last_email_orch else None
                _has_email_ctx = bool(_last_email_orch) or bool(_search_res_orch)
                if _has_email_ctx:
                    _ORCH_REPLY_RE = re.compile(
                        r"\b(rep(?:ly|lies|ond|onse|pond|ponse)|respond|response|reply"
                        r"|write\s+back|give\s+.{0,20}(reply|response|answer)"
                        r"|draft\s+.{0,15}(reply|response)"
                        r"|above\s+(mail|email)|tell\s+(him|her|them)"
                        r"|reply\s+to|respond\s+to)\b",
                        re.IGNORECASE,
                    )
                    _forced_email_reply = False
                    if _ORCH_REPLY_RE.search(text):
                        _forced_email_reply = True
                        log.info(
                            "[ORCHESTRATOR] Email-context safety net: intent %s -> EMAIL_REPLY "
                            "(email_ctx=True, reply-command matched, input=%r)",
                            intent, text[:50],
                        )
                    else:
                        # Also force EMAIL_REPLY when the user typed a reply-content
                        # *statement* (e.g. "I will be available") and email context exists.
                        # Import lazily to avoid circular dependency.
                        try:
                            from core.intent_classifier import IntentClassifier as _IC
                            if _IC._is_reply_content_statement(text.strip().lower(), email_context=True):
                                _forced_email_reply = True
                                log.info(
                                    "[ORCHESTRATOR] Forced EMAIL_REPLY due to reply-content context "
                                    "(intent was %s, email_ctx=True, input=%r)",
                                    intent, text[:50],
                                )
                        except Exception as _ic_exc:
                            log.debug("reply-content check failed: %s", _ic_exc)
                    if _forced_email_reply:
                        intent = "EMAIL_REPLY"
            except Exception as _oex:
                log.debug("Email-context safety net check failed: %s", _oex)

        # Safety override: when access control has already resolved a folder scope,
        # any non-document intent is almost certainly a misclassification.
        _DOCUMENT_INTENTS = {
            "RETRIEVAL", "DOCUMENT_SEARCH", "DOCUMENT_FOLDER_QUERY",
            "SUMMARY", "DOCUMENT_SUMMARY", "DOCUMENT_LIST", "TOPIC", "COMPARE",
            "FILE_SEARCH", "FILE_LIST", "FILE_SELECT",
        }
        if _forced_folder and intent not in _DOCUMENT_INTENTS:
            intent = "DOCUMENT_FOLDER_QUERY"
            log.debug(
                "Intent overridden to DOCUMENT_FOLDER_QUERY because folder_path=%r is active "
                "(was: %s)", _forced_folder, intent,
            )

        # Document-domain safety override: prevent EMAIL_SEARCH when the query
        # contains file/folder/document keywords but NO email-domain keywords.
        _DOC_KW_RE = re.compile(
            r"\b(files?|documents?|docs?|folder|directory|summarize|summarise)\b",
            re.IGNORECASE,
        )
        _EMAIL_STRICT_RE = re.compile(
            r"\b(email|mail|inbox|gmail|outlook)\b",
            re.IGNORECASE,
        )
        if intent == "EMAIL_SEARCH" and _DOC_KW_RE.search(text) and not _EMAIL_STRICT_RE.search(text):
            _corrected = "DOCUMENT_LIST" if not _forced_folder else "DOCUMENT_FOLDER_QUERY"
            log.info(
                "[ORCHESTRATOR] Corrected EMAIL_SEARCH -> %s (doc keywords, no email keyword | %r)",
                _corrected, text[:60],
            )
            intent = _corrected

        # Multi-intent detection: "summarize [folder] … explain report.pdf"
        # When both a folder-level verb and a file-explain verb appear in the
        # same query alongside a filename, both ops are dispatched and combined.
        _multi_intent_file: Optional[str] = None
        if _forced_folder and intent in {"RETRIEVAL", "SUMMARY", "DOCUMENT_FOLDER_QUERY"}:
            _has_summary_verb = bool(re.search(
                r"\b(summarize|summarise|list\s+all|show\s+all|find\s+all)\b",
                text, re.IGNORECASE,
            ))
            _has_explain_verb = bool(re.search(
                r"\b(explain|describe|analyze|analyse|tell\s+me\s+about)\b",
                text, re.IGNORECASE,
            ))
            if _has_summary_verb and _has_explain_verb:
                try:
                    from agents.knowledge.retrieval_agent import _extract_filename_from_query
                    _multi_intent_file = _extract_filename_from_query(text)
                except Exception:
                    _multi_intent_file = None

        # ── 5. Route intent → tool_name ─────────────────────────────────────
        # Enrichment / folder-resolution rules:
        #   a) Folder scope active → keep text clean; folder passed via ctx kwargs.
        #   b) DOCUMENT_FOLDER_QUERY with no folder → fall back to last_folder
        #      memory, or return a clarification prompt immediately.
        #   c) Context follow-up (no folder scope) → inject last_file for retrieval.
        enriched_text = text
        if intent in {"RETRIEVAL", "SUMMARY", "DOCUMENT_SUMMARY", "DOCUMENT_FOLDER_QUERY"}:
            # ── File-index fast-path ───────────────────────────────────────────
            # When the query mentions a known filename *and* no folder scope is
            # active yet, resolve the full path directly from the session file
            # index and answer from disk.  This eliminates the need to repeat a
            # folder path for files that were already granted access to.
            if not _forced_folder and memory is not None:
                try:
                    _fi_path = memory.lookup_file(text)
                    if _fi_path and os.path.isfile(_fi_path):
                        log.info("[ORCHESTRATOR] File-index fast-path: %r", _fi_path)
                        _fi_resp = self._handle_file_qa(_fi_path, text)
                        _fi_resp.intent = intent
                        _fi_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                        self._post_process(text, _fi_resp, tool_name="FILE_QA")
                        return _fi_resp
                except Exception as _fie:
                    log.debug("[ORCHESTRATOR] File-index fast-path failed: %s", _fie)

            if _forced_folder:
                log.info("Folder-scoped query — folder=%r will filter retrieval", _forced_folder)
                # Named-file disk-first: "summarize CheckToken.js", "explain BookmarkToggle.js"
                # Works even when the file has never been indexed in the vector store.
                if intent in {"SUMMARY", "DOCUMENT_SUMMARY", "RETRIEVAL", "DOCUMENT_FOLDER_QUERY"}:
                    try:
                        from agents.knowledge.retrieval_agent import (
                            _extract_filename_from_query,
                            _find_file_under_root,
                        )
                        _fname_q = _extract_filename_from_query(text)
                        if _fname_q:
                            _fq_disk = _find_file_under_root(_forced_folder, _fname_q)
                            if _fq_disk and os.path.isfile(_fq_disk):
                                log.info(
                                    "Named-file disk-first (intent=%s): %r",
                                    intent, _fq_disk,
                                )
                                _fqa_resp = self._handle_file_qa(_fq_disk, text)
                                _fqa_resp.intent = intent
                                _fqa_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                                self._post_process(text, _fqa_resp, tool_name="FILE_QA")
                                return _fqa_resp
                    except Exception:
                        pass
            elif intent == "DOCUMENT_FOLDER_QUERY":
                # Must have a folder — try last_folder from session memory
                if memory is not None:
                    try:
                        _last_fol = memory.get_last_folder()
                        if _last_fol:
                            _forced_folder = _last_fol
                            _skip_last_file = True
                            log.info(
                                "DOCUMENT_FOLDER_QUERY: resolved folder from memory=%r",
                                _forced_folder,
                            )
                    except Exception:
                        pass
                if not _forced_folder:
                    _no_folder_resp = AgentResponse(
                        answer="Please specify which folder you want to search.",
                        intent="DOCUMENT_FOLDER_QUERY",
                    )
                    _no_folder_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                    self._post_process(text, _no_folder_resp)
                    return _no_folder_resp
            elif intent in {"SUMMARY", "DOCUMENT_SUMMARY"} and _is_context_followup(text):
                # "summarize the above file" / "summarize this file" / "summarize that document"
                # Priority: file-first → folder RAG fallback.
                # 1. If last_file is known and on disk → read it directly (no RAG needed).
                # 2. Else if last_folder is known → scope the summarize tool to that folder.
                # 3. Else enrich the query with the last filename for RAG.
                if memory is not None:
                    try:
                        _su_file = memory.get_last_file()
                        _su_fol  = memory.get_last_folder()
                    except Exception:
                        _su_file = None
                        _su_fol  = None
                    if _su_file and _su_fol:
                        _su_disk = os.path.join(_su_fol, _su_file)
                        if os.path.isfile(_su_disk):
                            log.info(
                                "SUMMARY context follow-up — disk-first: %r",
                                _su_disk,
                            )
                            _fqa_resp = self._handle_file_qa(_su_disk, text)
                            _fqa_resp.intent = intent
                            _fqa_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                            self._post_process(text, _fqa_resp, tool_name="FILE_QA")
                            return _fqa_resp
                        else:
                            # File known but not found at constructed path — report clearly
                            # instead of silently querying the vector store with wrong scope.
                            log.warning(
                                "[SUMMARY] last_file=%r not found at %r (folder=%r)",
                                _su_file, _su_disk, _su_fol,
                            )
                            _nf_resp = AgentResponse(
                                answer=(
                                    f"\u26a0\ufe0f Could not find **{_su_file}** at the last known "
                                    f"location:\n\U0001f4c1 {_su_disk}\n\n"
                                    "The file may have been moved or renamed. "
                                    "Please open or reference the file again."
                                ),
                                intent=intent,
                            )
                            _nf_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                            self._post_process(text, _nf_resp)
                            return _nf_resp
                    # File not on disk (or no file known) — fall back to folder scope
                    if _su_fol:
                        _forced_folder = _su_fol
                        _skip_last_file = True
                        log.info(
                            "SUMMARY context follow-up (folder fallback): folder=%r",
                            _forced_folder,
                        )
                    elif _su_file and _no_filename_in_query(text):
                        enriched_text = f"{text} {_su_file}"
                        log.info(
                            "SUMMARY context follow-up enriched with last_file=%r",
                            _su_file,
                        )
            elif not _skip_last_file and memory is not None:
                last_file = memory.get_last_file()
                if last_file and _is_context_followup(text) and _no_filename_in_query(text):
                    # Try direct disk-based answering first so un-indexed files
                    # always work on the FIRST attempt, regardless of RAG state.
                    _lfol = None
                    try:
                        _lfol = memory.get_last_folder()
                    except Exception:
                        pass
                    _disk_path = (
                        os.path.join(_lfol, last_file)
                        if _lfol else last_file
                    )
                    if os.path.isfile(_disk_path):
                        log.info(
                            "Context follow-up — direct file Q&A: %r",
                            _disk_path,
                        )
                        _fqa_resp = self._handle_file_qa(_disk_path, text)
                        _fqa_resp.intent = intent
                        _fqa_resp.latency_ms = (time.perf_counter() - t0) * 1_000
                        self._post_process(text, _fqa_resp, tool_name="FILE_QA")
                        return _fqa_resp
                    # File not on disk: fall back to folder-scoped RAG
                    enriched_text = f"{text} {last_file}"
                    if _lfol:
                        _forced_folder = _lfol
                        _skip_last_file = True
                    log.info(
                        "Context follow-up (RAG fallback) — enriched with "
                        "last_file=%r, folder=%r",
                        last_file, _forced_folder,
                    )

        router = _get_router()
        tool_name: Optional[str] = None
        if router is not None:
            try:
                tool_name = router.route(intent)
            except Exception as exc:
                log.debug("Router.route failed: %s", exc)

        # ── 6. Execute tool (if any) ─────────────────────────────────────────
        # Priority: multi-intent fast-path → folder-scoped tool → normal tool → LLM
        tool_result = None

        # Multi-intent fast-path: folder summary + file explain → combined response
        if _multi_intent_file and _forced_folder:
            resp = self._handle_combined_folder_file(text, _forced_folder, _multi_intent_file)
            resp.intent = intent
            resp.latency_ms = (time.perf_counter() - t0) * 1_000
            self._post_process(text, resp, tool_name="MULTI_INTENT")
            return resp

        if tool_name is not None:
            tool_executor = _get_tool_executor()
            if tool_executor is not None:
                try:
                    # Propagate folder scope so document tools can filter by folder
                    _exec_ctx: dict = {}
                    if _forced_folder and tool_name in {
                        "documents.search", "documents.summarize",
                        "documents.list", "documents.topics",
                    }:
                        _exec_ctx["folder_path"] = _forced_folder
                        log.info(
                            "[ORCHESTRATOR] Tool=%r using folder_path=%r",
                            tool_name, _forced_folder,
                        )
                    tool_result = tool_executor.execute(tool_name, enriched_text, **_exec_ctx)
                    # Track folder/file context for DOCUMENT_LIST so follow-up
                    # questions ("summarize this file") work from the tool path.
                    if (
                        tool_result is not None
                        and tool_result.success
                        and tool_name == "documents.list"
                        and _forced_folder
                        and os.path.isdir(_forced_folder)
                    ):
                        _list_mem = _get_memory()
                        if _list_mem is not None:
                            try:
                                _list_mem.set_last_folder(_forced_folder)
                                _all_files = [
                                    e for e in os.listdir(_forced_folder)
                                    if os.path.isfile(os.path.join(_forced_folder, e))
                                ]
                                if len(_all_files) == 1:
                                    _list_mem.set_last_file(_all_files[0])
                                    log.info(
                                        "DOCUMENT_LIST (tool path): auto-selected "
                                        "last_file=%r in folder=%r",
                                        _all_files[0], _forced_folder,
                                    )
                                else:
                                    log.info(
                                        "DOCUMENT_LIST (tool path): last_folder=%r "
                                        "(%d files)",
                                        _forced_folder, len(_all_files),
                                    )
                            except Exception as _lm_exc:
                                log.debug("DOCUMENT_LIST memory tracking failed: %s", _lm_exc)
                except Exception as exc:
                    log.exception("ToolExecutor.execute(%r) raised: %s", tool_name, exc)

        # ── 7. Build response ────────────────────────────────────────────────
        try:
            if tool_result is not None and tool_result.success:
                # Tool returned a valid answer — wrap it
                # Do NOT bullet-ify summary/explain answers; preserve full text.
                try:
                    from agents.knowledge.retrieval_agent import _is_summary_intent
                    _suppress_bullets = _is_summary_intent(enriched_text)
                except Exception:
                    _suppress_bullets = False
                bullets = [] if _suppress_bullets else (_to_bullets(tool_result.output) if tool_result.output else [])
                resp = AgentResponse(
                    answer=tool_result.output or "",
                    intent=intent,
                    source=tool_result.source,
                    bullets=bullets,
                )
            else:
                # No tool hit or tool failed — fall through to legacy dispatch
                resp = self._dispatch(intent, enriched_text, memory=memory, folder_path=_forced_folder, style_hint=_style_hint)
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
        """Try IntentClassifier first; fall back to planner_agent.
        
        CRITICAL: Pass conversation history in structured format (list[dict]) with full
        message content so the LLM can perform intelligent context-aware inference.
        """
        # Build history list for context (last 6 turns) in STRUCTURED format
        # IMPORTANT: Pass raw list[dict] with role/content, NOT formatted strings
        history: list[dict] = []
        facts: dict = {}
        last_file: Optional[str] = None
        last_intent: Optional[str] = None
        if memory is not None:
            try:
                # Get raw history as list[dict] with role/content keys
                raw_history = memory.get_history(last_n=6)
                history = raw_history if raw_history else []
                facts = memory.list_facts()
                last_file = memory.get_last_file()
                last_intent = memory.get_last_intent()
            except Exception:
                pass

        # DEBUG: Log the history being passed to intent classifier
        log.info("[DEBUG] _classify_intent: Classifying '%s'", text[:60])
        log.info("[DEBUG] _classify_intent: History items: %d", len(history))
        for i, turn in enumerate(history[-3:]):  # Show last 3 turns
            content_preview = turn.get('content', '')[:80] if isinstance(turn, dict) else str(turn)[:80]
            role = turn.get('role', '?') if isinstance(turn, dict) else '?'
            log.info("[DEBUG]   Turn %d: %s - %s", i, role, content_preview)
        if facts:
            log.info("[DEBUG] Memory facts: %s", facts)

        clf = _get_intent_classifier()
        if clf is not None:
            try:
                result = clf.classify(
                    text,
                    history=history,  # NOW: list[dict] with role/content (not formatted strings)
                    memory_facts=facts,
                    last_intent=last_intent,
                    last_file=last_file,
                )
                log.info("[DEBUG] Intent classified as: %s", result)
                return result
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

    # Intents whose answers must NOT be stored in conversational history
    # because they are system/agent outputs, not natural conversation.
    _NON_CONVERSATIONAL_INTENTS: frozenset[str] = frozenset({
        "EMAIL_SUMMARY", "EMAIL_SEARCH", "EMAIL_REPLY", "EMAIL_SEND",
        "EMAIL_DRAFT", "EMAIL_FORWARD",
        "REMINDER_SET", "REMINDER_LIST", "REMINDER_DELETE",
        "DOCUMENT_LIST", "RETRIEVAL", "SUMMARY", "TOPIC",
        "AUDIO_TRANSCRIBE", "AUDIO_QUERY", "AUDIO_LIST",
        "FILE_QA", "FILE_SEARCH", "FILE_LIST", "FILE_SELECT",
        "FILE_DISAMBIG", "COMPARE",
        "PERMISSION_GRANTED", "PERMISSION_DENIED", "PERMISSION_EXPIRED",
        "NO_PENDING_PERMISSION", "REQUEST_PERMISSION",
        "MEMORY_STORE", "MEMORY_RECALL",
        "TIME", "DATE", "GREETING",
        "EMPTY", "ERROR", "INVALID_INPUT", "UNKNOWN",
    })

    @staticmethod
    def _is_clean_assistant_content(text: str) -> bool:
        """Return True when *text* is safe to store as a conversational turn.

        Rejects system/agent pipeline outputs that should never appear in the
        LLM's conversational context.  Returns False for:
        - Email agent notices  ("No draft email", "search for emails", …)
        - Reminder / document system messages
        - Error / status lines
        - Any content that starts with a structured output marker (emoji, ✅ ❌ ⚠️)
        """
        import re
        if not text or not text.strip():
            return False

        t = text.strip()

        # ── Reject by prefix patterns ────────────────────────────────────────
        _NOISE_PREFIXES = (
            # Email agent
            "No draft email",
            "no draft email",
            "First generate a reply",
            "first generate a reply",
            "search for emails",
            "Search for emails",
            "Searching emails",
            "searching emails",
            "Fetching latest emails",
            "fetching latest emails",
            "Found ",          # "Found 3 emails from …"
            "No emails found",
            # Reminder agent
            "Reminder set",
            "Reminder canceled",
            "Reminder deleted",
            "I could not understand the reminder",
            "No reminders",
            # Document / retrieval agent
            "No documents found",
            "No relevant information",
            "I cannot access information from",
            # System / permission
            "✅ Access granted",
            "Access request denied",
            "⏰ The previous permission",
            "There is no pending permission",
            # Error markers
            "⚠️",
            "❌",
            "[Error]",
            "[Warning]",
            "_(no response)_",
        )
        for prefix in _NOISE_PREFIXES:
            if t.startswith(prefix):
                return False

        # ── Reject by inline pattern (anywhere in the text) ──────────────────
        _NOISE_PATTERNS = [
            r"^\d+\s+email",              # "3 emails from alice"
            r"No draft email",
            r"search for (?:emails?|ideas)",
            r"generate a reply",
            r"\[Email\]",
            r"\[Reminder\]",
            r"\[Info\]",
            r"\[Warning\]",
            r"\[Ready\]",
            r"\[MCP\]",
            r"Planner Decision:",          # CLI debug line
            r"^Assistant:",               # CLI echo
        ]
        for pat in _NOISE_PATTERNS:
            if re.search(pat, t, re.IGNORECASE):
                return False

        # ── Reject if content is purely structured (bullet/numbered list of
        #    file/email results — not natural language) ─────────────────────
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        if lines and len(lines) >= 3:
            structured = sum(
                1 for l in lines
                if re.match(r'^(\d+\.|[-•*]|\[\d+\]|📧|📄|🔔)', l)
            )
            if structured / len(lines) >= 0.6:  # 60%+ structured lines = system output
                return False

        return True

    def _post_process(
        self,
        user_input: str,
        resp: AgentResponse,
        tool_name: Optional[str] = None,
        tool_result=None,
    ) -> None:
        """Store conversation turn, update session context, and emit structured log."""
        if resp.answer:
            self._last_response = resp.answer
            memory = _get_memory()
            if memory is not None:
                try:
                    memory.set_last_response(resp.answer)
                except Exception:
                    pass
            
        memory = _get_memory()
        if memory is not None:
            try:
                memory.add_turn("user", user_input)
                # Only store assistant turns that are clean conversational content.
                # System/agent outputs (email notices, reminder confirmations, etc.)
                # are filtered here so they never pollute the LLM's context window.
                _is_conversational_intent = resp.intent not in self._NON_CONVERSATIONAL_INTENTS
                if resp.answer and _is_conversational_intent and self._is_clean_assistant_content(resp.answer):
                    memory.add_turn("assistant", resp.answer[:500])
            except Exception as exc:
                log.debug("memory.add_turn failed: %s", exc)

            # Update session context: last referenced file and last intent
            try:
                if resp.intent not in {"CHAT", "GREETING", "GENERAL", "TIME", "DATE", "EMPTY",
                                       "MEMORY_STORE", "MEMORY_RECALL", "INVALID_INPUT"}:
                    memory.set_last_intent(resp.intent)
                if resp.source:
                    # source may be a comma-separated list; take the first entry
                    first_source = resp.source.split(",")[0].strip()
                    if first_source:
                        memory.set_last_file(first_source)
                        log.debug("Memory: last_file updated to %r", first_source)
            except Exception as exc:
                log.debug("memory context update failed: %s", exc)

            # Record (query, intent) pattern for future boosting
            try:
                if resp.intent not in {"CHAT", "GREETING", "GENERAL", "TIME", "DATE", "EMPTY"}:
                    memory.record_pattern(user_input, resp.intent)
            except Exception as exc:
                log.debug("memory.record_pattern failed: %s", exc)

        # Record action sequence in learning service
        _ls = _get_learning_service()
        if _ls is not None:
            try:
                if resp.intent not in {"CHAT", "GREETING", "GENERAL", "TIME", "DATE", "EMPTY"}:
                    _ls.record_action_sequence([resp.intent])
            except Exception as _ls_exc:
                log.debug("learning record_action_sequence failed: %s", _ls_exc)

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

    def _handle_kg_context_response(
        self, text: str, kg_context: str, memory=None
    ) -> AgentResponse:
        """Answer *text* using facts from the Knowledge Graph.

        Injects KG context into the LLM system prompt and instructs the model
        to answer ONLY from those facts, preventing hallucination.
        """
        import ollama as _ollama_kg
        from agents.core.general_agent import build_graph_prompt

        print(f"[KG] Final context sent to LLM:\n{kg_context}")
        messages = build_graph_prompt(text, kg_context)

        try:
            print(f"[LLM] Using model: {settings.model_name}")
            resp = _ollama_kg.chat(
                model=settings.model_name,
                options={"temperature": 0.3, "num_predict": 300},
                messages=messages,
            )
            answer = resp["message"]["content"]
        except Exception as exc:
            log.warning("[KG] LLM call with graph context failed: %s", exc)
            answer = "I found relevant facts in my knowledge graph but could not generate a response."

        return AgentResponse(answer=answer, intent="KNOWLEDGE_GRAPH")

    def _dispatch(self, intent: str, text: str, memory=None, folder_path: Optional[str] = None, style_hint: str = "") -> AgentResponse:  # noqa: C901
        log.debug("[ORCHESTRATOR] Dispatching intent '%s' to fallback handler.", intent)
        match intent:
            case "CHAT":
                return self._handle_chat(text, memory=memory)
            case "GREETING":
                return AgentResponse(
                    answer="Hello! How can I help you today?",
                    intent="GREETING",
                )
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
            case "INVALID_INPUT":
                return AgentResponse(
                    answer="I didn't understand that. Could you rephrase?",
                    intent="INVALID_INPUT",
                )
            case "MEMORY_STORE":
                return self._handle_memory_store(text, memory=memory)
            case "MEMORY_RECALL":
                return self._handle_memory_recall(text, memory=memory)
            case _:
                # All other intents, including failed tool executions and unknown
                # intents mapped to GENERAL, are handled here.
                log.info(
                    "[ORCHESTRATOR] Intent '%s' has no specific fallback handler, "
                    "routing to general AI response.", intent
                )
                return self._handle_general(
                    text, memory=memory, folder_path=folder_path, style_hint=style_hint
                )

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

    def _handle_general_ai_response(self, text: str, memory=None) -> AgentResponse:
        """Pure LLM handler for GENERAL_KNOWLEDGE queries — no file/vector access.

        Called by the step-2.1 fast-path when classify_query() returns
        GENERAL_KNOWLEDGE.  Delegates to ``handle_general_ai`` which uses a
        ChatGPT-style system prompt that explicitly forbids file references.
        Memory facts and conversation history are injected for continuity.
        """
        from agents.core.general_agent import handle_general_ai

        system_extra = ""
        if memory is not None:
            try:
                summary = memory.facts_summary()
                if summary:
                    system_extra = f"\n\n{summary}"
            except Exception:
                pass

        # Pass memory so history + topic/goal context are injected into the LLM call
        print("[DEBUG] LLM call count = 1")
        answer = handle_general_ai(
            text, settings.model_name, system_extra=system_extra, memory=memory
        )
        return AgentResponse(answer=answer or "", intent="GENERAL")

    def _handle_chat(self, text: str, memory=None) -> AgentResponse:
        from agents.core.general_agent import handle_general

        # Build memory-enriched system prompt (facts summary)
        system_extra = ""
        if memory is not None:
            try:
                summary = memory.facts_summary()
                if summary:
                    system_extra = f"\n\n{summary}"
            except Exception:
                pass

        # Pass memory so history + topic/goal context are injected into the LLM call
        answer = handle_general(
            text, settings.model_name, system_extra=system_extra, memory=memory
        )
        return AgentResponse(answer=answer or "")

    def _handle_general(self, text: str, memory=None, folder_path: Optional[str] = None, style_hint: str = "") -> AgentResponse:
        """Try doc retrieval first; fall back to LLM with memory context."""
        from agents.core.general_agent import handle_general
        from agents.knowledge.retrieval_agent import (
            _query_references_unauthorized_path,
            _get_authorized_docs_root,
            _is_summary_intent,
        )

        # ── Block unauthorized path references before any LLM call ──────────
        if _query_references_unauthorized_path(text):
            authorized = _get_authorized_docs_root()
            return AgentResponse(
                answer=(
                    f"I cannot access information from that location. "
                    f"I can only access documents inside {authorized}."
                )
            )

        # ── Skip vector retrieval for clear general-knowledge queries ─────────
        # "explain machine learning", "what is AI", "how does TCP work" must
        # answer from LLM only — not from indexed documents.  If step-2.1 fast-
        # path somehow didn't fire (e.g. exception), this guard provides safety.
        if not folder_path:
            try:
                from core.intent_classifier import intent_classifier as _ic_hg
                if _ic_hg.classify_query(text) == "GENERAL_KNOWLEDGE":
                    _hg_mem = memory
                    _sys_extra_gk = ""
                    if _hg_mem is not None:
                        try:
                            _s = _hg_mem.facts_summary()
                            if _s:
                                _sys_extra_gk = f"\n\n{_s}"
                        except Exception:
                            pass
                    if style_hint:
                        _sys_extra_gk += f"\n\n{style_hint}"
                    from agents.core.general_agent import handle_general_ai
                    # Pass memory so history + topic/goal context are injected
                    _ans = handle_general_ai(
                        text, settings.model_name,
                        system_extra=_sys_extra_gk, memory=_hg_mem,
                    )
                    return AgentResponse(answer=_ans or "")
            except Exception as _hge:
                log.debug("[GENERAL] GK skip-retrieval guard failed: %s", _hge)

        # ── Check if this is a pure conversational query (no file context) ────
        # For GENERAL queries with no selected file and no folder scope,
        # use LLM directly without vector retrieval to avoid irrelevant results.
        _has_file_context = False
        _last_file_for_followup = None
        if memory is not None:
            _last_file_for_followup = memory.get_last_file()
            # Only consider it "file context" if there's an actual follow-up pattern
            if _last_file_for_followup and _is_context_followup(text) and _no_filename_in_query(text):
                _has_file_context = True

        # ── PRIORITY 1: Pure LLM for conversational queries (no file/folder scope) ──
        # When there's no file context and no folder restriction, answer from LLM only.
        # This prevents vector store pollution for open-ended queries like
        # "research on plants", "explain machine learning", etc.
        if not _has_file_context and not folder_path:
            log.info(
                "[GENERAL] Using pure LLM (no file context, no folder scope) for: %r",
                text[:60],
            )
            system_extra = ""
            if memory is not None:
                try:
                    summary = memory.facts_summary()
                    if summary:
                        system_extra = f"\n\n{summary}"
                except Exception:
                    pass
            if style_hint:
                system_extra = system_extra + f"\n\n{style_hint}"
            # Pass memory so history + topic/goal context are injected
            answer = handle_general(
                text, settings.model_name, system_extra=system_extra, memory=memory
            )
            return AgentResponse(answer=answer or "")

        # ── PRIORITY 2: Retrieval only when file context exists ──────────────────
        enriched = text
        if _has_file_context:
            enriched = f"{text} {_last_file_for_followup}"
            log.info(
                "GENERAL context follow-up — enriched query with last_file=%r",
                _last_file_for_followup,
            )

        db = self._get_vector_db()
        win_docs_db = self._get_win_docs_db()
        extra = [win_docs_db] if win_docs_db is not None else []

        if (db is not None or extra) and (_has_file_context or folder_path):
            try:
                from agents.knowledge.retrieval_agent import handle_retrieval
                ans, src = handle_retrieval(
                    enriched, db, settings.retrieval_threshold, settings.model_name,
                    extra_dbs=extra,
                    last_file=(_last_file_for_followup if _has_file_context else None),
                    folder_path=folder_path,
                )
                if ans:
                    bullets = [] if _is_summary_intent(enriched) else _to_bullets(ans)
                    return AgentResponse(
                        answer=ans,
                        source=src,
                        bullets=bullets,
                    )
            except Exception as exc:
                log.debug("Retrieval failed in GENERAL handler: %s", exc)

        # ── PRIORITY 3: LLM fallback when retrieval failed or no results ────────
        system_extra = ""
        if memory is not None:
            try:
                summary = memory.facts_summary()
                if summary:
                    system_extra = f"\n\n{summary}"
            except Exception:
                pass
        if style_hint:
            system_extra = system_extra + f"\n\n{style_hint}"

        # Pass memory so history + topic/goal context are injected
        answer = handle_general(
            text, settings.model_name, system_extra=system_extra, memory=memory
        )
        return AgentResponse(answer=answer or "")

    # ── MEMORY STORE / RECALL ────────────────────────────────────────────────

    def _handle_memory_store(self, text: str, memory=None) -> AgentResponse:
        """Explicitly store a user fact from a 'remember that …' query."""
        mem = memory or _get_memory()
        if mem is None:
            return AgentResponse(
                answer="Memory service is unavailable.", intent="MEMORY_STORE"
            )
        # extract_and_store already handles "remember that my X is Y" patterns
        found = mem.extract_and_store(text)
        if found:
            items = "; ".join(f"{k} = {v}" for k, v in found.items())
            return AgentResponse(
                answer=f"Got it! I've remembered: {items}.",
                intent="MEMORY_STORE",
            )
        # Fallback: store the raw statement under a generic key
        clean = re.sub(
            r"^\s*(?:remember\s+(?:that\s+)?|note\s+(?:that\s+)?|"
            r"save\s+(?:that\s+)?|keep\s+in\s+mind\s+(?:that\s+)?)",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if clean:
            mem.store("note", clean)
            return AgentResponse(
                answer=f"Got it! I've noted: \"{clean}\".",
                intent="MEMORY_STORE",
            )
        return AgentResponse(
            answer="I couldn't figure out what to remember. Try: "
                   "\"Remember that my favorite language is Python\".",
            intent="MEMORY_STORE",
        )

    def _handle_memory_recall(self, text: str, memory=None) -> AgentResponse:
        """Answer a memory-recall query from stored facts."""
        mem = memory or _get_memory()
        if mem is None:
            return AgentResponse(
                answer="Memory service is unavailable.", intent="MEMORY_RECALL"
            )
        answer = mem.recall_for_query(text)
        if answer:
            return AgentResponse(answer=answer, intent="MEMORY_RECALL")
        # No stored facts match — try the LLM with facts injected
        facts = mem.list_facts()
        if facts:
            facts_str = "\n".join(f"  {k}: {v}" for k, v in facts.items())
            return AgentResponse(
                answer=(
                    f"Here is what I know about you:\n{facts_str}\n\n"
                    "I don't have a specific answer to that question in my memory."
                ),
                intent="MEMORY_RECALL",
            )
        return AgentResponse(
            answer=(
                "I don't have any facts stored about you yet. "
                "Tell me something like: \"Remember that my name is Alice\" "
                "and I'll remember it for this session."
            ),
            intent="MEMORY_RECALL",
        )

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
        try:
            from agents.knowledge.email_summarizer_agent import handle_email_summary
        except ImportError:
            return AgentResponse(answer="Email service is currently unavailable.")
        return AgentResponse(answer=handle_email_summary() or "No emails to summarise.")

    def _handle_email_query(self, text: str) -> AgentResponse:
        """Answer a factual question about the email currently in memory.

        Reads ``last_email`` from conversation memory and answers directly
        (sender, date, subject, etc.).  Falls through to ``_handle_email_search``
        only when no email context exists in memory.
        """
        try:
            from memory.conversation_memory import conversation_memory
            last_email = conversation_memory.get_last_email()
            if last_email:
                t = text.lower()
                if re.search(r"\b(who|from\b|sender)\b", t):
                    answer = f"This email was sent by: {last_email.get('from', 'Unknown')}"
                elif re.search(r"\b(when|date|received|arrived?)\b", t):
                    answer = f"This email was received on: {last_email.get('date', 'Unknown')}"
                elif re.search(r"\b(subject|topic|title|about)\b", t):
                    answer = f"Subject: {last_email.get('subject', '(no subject)')}"
                elif re.search(r"\b(to\b|recipient|receiver)\b", t):
                    answer = f"This email was addressed to: {last_email.get('to', 'Unknown')}"
                else:
                    body = (last_email.get("body") or "").strip()
                    answer = (
                        f"Email from {last_email.get('from', '?')}\n"
                        f"Subject  : {last_email.get('subject', '(no subject)')}\n"
                        f"Date     : {last_email.get('date', 'Unknown')}"
                    )
                    if body:
                        preview = body[:400] + ("..." if len(body) > 400 else "")
                        answer += f"\n\nContent:\n{preview}"
                log.info("[EMAIL_QUERY] Answered from memory")
                return AgentResponse(answer=answer, intent="EMAIL_QUERY")
        except Exception as exc:
            log.debug("[EMAIL_QUERY] Memory check in fallback failed: %s", exc)
        # No email in memory — fall through to search
        return self._handle_email_search(text)

    def _handle_email_search(self, text: str) -> AgentResponse:
        self._auto_fetch_emails(force=True)
        try:
            from agents.knowledge.email_summarizer_agent import summarize_emails_by_query
            answer = summarize_emails_by_query(text, max_results=8)
        except ImportError:
            return AgentResponse(answer="Email service is currently unavailable.")
        except Exception:
            try:
                from agents.knowledge.email_query_agent import search_emails_by_text
                results = search_emails_by_text(text)
            except ImportError:
                return AgentResponse(answer="Email service is currently unavailable.")
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

    # ── FILE SEARCH / SELECT ──────────────────────────────────────────────────

    def _handle_file_search(self, text: str) -> AgentResponse:
        """Search the SQLite file index and return a conversational file listing.

        Flow
        ----
        1. Query ``file_search_service.search(keywords)``.
        2. Zero results → helpful not-found message (suggests granting access).
        3. One result  → auto-select; call ``_handle_selected_file``.
        4. Multiple + summarize/multi-file verb → auto-summarize all results.
        5. Multiple    → format numbered list; store pending selection in memory.
        """
        # Clear stale file context so the new search runs globally
        _fs_ctx = _get_memory()
        if _fs_ctx is not None:
            _fs_ctx.set_selected_file("")
            _fs_ctx.set_last_file("")
            _fs_ctx.clear_pending_documents()

        try:
            from services.file_search_service import file_search_service
        except ImportError as exc:
            log.error("[FILE_SEARCH] file_search_service unavailable: %s", exc)
            return AgentResponse(
                answer="File search service is temporarily unavailable.",
                intent="FILE_SEARCH",
            )

        # Check index is populated at all
        if file_search_service.is_index_empty():
            return AgentResponse(
                answer=(
                    "\u26a0\ufe0f I haven't been given access to any folders yet, so I have "
                    "no files indexed.\n\n"
                    "Please tell me which folder to search — for example:\n"
                    "\U0001f4c1 **\"Use folder C:\\Users\\...\\Documents\"**"
                ),
                intent="FILE_SEARCH",
            )

        results = file_search_service.search(text)

        if not results:
            # Broaden: try just the most content-rich token
            from services.file_search_service import _extract_keywords
            kw = _extract_keywords(text)
            if kw and kw != text:
                results = file_search_service.search(kw)

        if not results:
            from services.file_search_service import _extract_keywords, _extract_entity_and_keyword
            kw = _extract_keywords(text)
            entity, _ = _extract_entity_and_keyword(text)
            
            if entity:
                return AgentResponse(
                    answer=f"No files found matching '{kw}'",
                    intent="FILE_SEARCH",
                )
            else:
                return AgentResponse(
                    answer=(
                        f"\U0001f50d No files matched **{text!r}** in the index.\n\n"
                        "Suggestions:\n"
                        "- Try a different keyword (e.g. just \"resume\" instead of \"my resume\")\n"
                        "- Ask me to **list all files** to see what's available"
                    ),
                    intent="FILE_SEARCH",
                )

        if len(results) == 1:
            # Single match — auto-select and proceed
            log.info("[FILE_SEARCH] Single result, auto-selecting: %r", results[0]["path"])
            return self._handle_selected_file(results[0]["path"], text)

        # Detect "summarize/explain/analyze multiple files" intent
        _MULTI_SUMMARIZE_RE = re.compile(
            r"\b(?:summarize|summarise|summarize\s+(?:all|them|each)|"
            r"give\s+(?:me\s+)?(?:a\s+)?summary|explain\s+(?:all|them)|"
            r"analyze|analyse|overview\s+of\s+(?:all|them)|"
            r"and\s+summarize|then\s+summarize)\b",
            re.IGNORECASE,
        )
        if _MULTI_SUMMARIZE_RE.search(text):
            # Auto-summarize the top results (cap at 4 text files)
            from agents.knowledge.retrieval_agent import (
                _load_document_from_path,
                _answer_from_file,
            )
            _IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
            text_results = [
                r for r in results
                if os.path.splitext(r.get("name", ""))[1].lower() not in _IMAGE_EXTS
            ][:4]
            if text_results:
                log.info(
                    "[FILE_SEARCH] Multi-file auto-summarize: %d files",
                    len(text_results),
                )
                parts: list[str] = []
                names: list[str] = []
                for r in text_results:
                    fp = r["path"]
                    if not os.path.isfile(fp):
                        continue
                    content = _load_document_from_path(fp)
                    if content and not content.startswith("__IMAGE"):
                        fname = os.path.basename(fp)
                        names.append(fname)
                        parts.append(f"=== {fname} ===\n{content[:4_000]}")
                if parts:
                    combined = "\n\n".join(parts)
                    answer = answer_from_file(
                        settings.model_name,
                        text,
                        combined,
                        " and ".join(names),
                        is_summary=True,
                    )
                    return AgentResponse(
                        answer=answer,
                        intent="FILE_SEARCH",
                        source=", ".join(r["path"] for r in text_results),
                    )

        # Multiple matches — present numbered list and wait for selection
        mem = _get_memory()
        if mem is not None:
            mem.set_pending_file_selection(results, original_query=text)
            mem.set_pending_documents(results)
            log.info(
                "[FILE_SEARCH] %d candidates stored in pending selection", len(results)
            )

        listing = file_search_service.format_listing(results)
        return AgentResponse(answer=listing, intent="FILE_SEARCH")

    def _handle_file_list(self) -> AgentResponse:
        """List indexed files from the SQLite index.

        When the user has previously selected a folder (``last_folder`` in
        session memory) only files inside that folder are returned.  Without
        a folder context every indexed file is shown.
        """
        try:
            from services.file_search_service import file_search_service
        except ImportError as exc:
            log.error("[FILE_LIST] file_search_service unavailable: %s", exc)
            return AgentResponse(
                answer="File search service is temporarily unavailable.",
                intent="FILE_LIST",
            )

        if file_search_service.is_index_empty():
            return AgentResponse(
                answer=(
                    "\u26a0\ufe0f No files are indexed yet.\n\n"
                    "Grant me access to a folder first \u2014 e.g. "
                    "\"Use folder C:\\Users\\...\\Documents\""
                ),
                intent="FILE_LIST",
            )

        # Respect the user's current folder context: if a folder was selected
        # this session, scope the listing to that folder only.
        mem = _get_memory()
        selected_folder: Optional[str] = mem.get_last_folder() if mem is not None else None

        print(f"[DEBUG] FILE_LIST — selected_folder: {selected_folder!r}")
        log.info("[FILE_LIST] selected_folder=%r", selected_folder)

        results = file_search_service.list_all(limit=30, folder_prefix=selected_folder)

        print(f"[DEBUG] FILE_LIST — files after filtering: {len(results)}")
        log.info("[FILE_LIST] files after filtering: %d", len(results))
        if not results and selected_folder:
            # Nothing in the chosen folder — tell the user clearly.
            return AgentResponse(
                answer=(
                    f"No indexed files found inside **{selected_folder}**.\n"
                    "Try indexing the folder first or say \"show all files\" "
                    "to list every indexed file."
                ),
                intent="FILE_LIST",
            )
        if not results:
            return AgentResponse(
                answer="No indexed files found.",
                intent="FILE_LIST",
            )

        if mem is not None and len(results) > 1:
            mem.set_pending_file_selection(results, original_query="list")
            mem.set_pending_documents(results)

        if selected_folder:
            header = (
                f"I have {len(results)} indexed file{'s' if len(results) != 1 else ''}"
                f" in **{selected_folder}**:"
            )
        else:
            header = f"I have {len(results)} indexed file{'s' if len(results) != 1 else ''}:"
        listing = file_search_service.format_listing(results, header=header)
        return AgentResponse(answer=listing, intent="FILE_LIST")

    def _handle_selected_file(
        self, file_path: str, original_query: str = ""
    ) -> AgentResponse:
        """Process the file selected by the user.

        Sets ``last_file`` and ``last_folder`` in memory, then decides the
        action based on *original_query*:

        - Summarize verb present → run ``_handle_file_qa`` with summary intent.
        - Read / open verb present → run ``_handle_file_qa`` with read intent.
        - No strong verb (bare "find") → confirm selection and ask what to do.
        """
        import os

        if not os.path.isfile(file_path):
            return AgentResponse(
                answer=(
                    f"\u26a0\ufe0f Could not find the file at:\n"
                    f"\U0001f4c1 {file_path}\n\n"
                    "It may have been moved or deleted."
                ),
                intent="FILE_SELECT",
            )

        fname = os.path.basename(file_path)
        folder = os.path.dirname(file_path)

        # Update memory context
        mem = _get_memory()
        if mem is not None:
            try:
                mem.set_last_file(fname)
                mem.set_last_folder(folder)
                mem.register_file(fname, file_path)
                mem.set_selected_file(file_path)
                log.info(
                    "[FILE_SELECT] last_file=%r last_folder=%r selected_file=%r",
                    fname, folder, file_path,
                )
            except Exception as _me:
                log.debug("[FILE_SELECT] memory update failed: %s", _me)

        # Update orchestrator context for absolute priority (PART 1 & 6)
        if file_path != self._active_file_path:
            self._active_file_path = file_path
            self._active_file_content = None # Reset (PART 1)
            self._last_response = None # Block stale data (PART 6)
            print(f"[FILE] Active file set: {fname}")

        q = original_query.lower()

        # Content action: summarize
        if re.search(
            r"\b(summarize|summarise|summary|overview|brief|gist|explain|describe)\b", q
        ):
            log.info("[FILE_SELECT] action=summarize for %r", fname)
            return self._handle_file_qa(file_path, original_query)

        # Content action: read / open
        if re.search(r"\b(read|open|view|show|display|content|text)\b", q):
            log.info("[FILE_SELECT] action=read for %r", fname)
            return self._handle_file_qa(file_path, original_query)

        # No strong verb — confirm selection and offer next steps
        return AgentResponse(
            answer=(
                f"\u2705 Selected: **{fname}**\n\n"
                f"\U0001f4c1 Location: {folder}\n\n"
                "What would you like to do?\n"
                "- Summarize it\n"
                "- Read its content\n"
                "- Ask a specific question about it"
            ),
            intent="FILE_SELECT",
            source=file_path,
        )

    def _handle_multi_file_qa(
        self, file_paths: list[str], query: str
    ) -> AgentResponse:
        """Answer *query* using content from two or more files combined.

        Loads each file (up to 6 000 chars each), concatenates under labelled
        headings, then uses :func:`_answer_from_file` for focused QA.
        Never returns a raw content dump.
        """
        from agents.knowledge.retrieval_agent import (
            _load_document_from_path,
            _answer_from_file,
        )
        parts: list[str] = []
        names: list[str] = []
        for fp in file_paths:
            content = _load_document_from_path(fp)
            if content:
                fname = os.path.basename(fp)
                names.append(fname)
                parts.append(f"=== {fname} ===\n{content[:6_000]}")
        if not parts:
            return AgentResponse(
                answer="\u26a0\ufe0f Could not read any of the specified files.",
                intent="FILE_QA",
            )
        combined = "\n\n".join(parts)
        answer = answer_from_file(
            settings.model_name,
            query,
            combined,
            " and ".join(names),
            is_summary=False,
        )
        return AgentResponse(
            answer=answer,
            intent="FILE_QA",
            source=", ".join(file_paths),
        )

    # ── DOCUMENTS ────────────────────────────────────────────────────────────

    def _handle_document_list(self, folder_path: Optional[str] = None) -> AgentResponse:
        from agents.knowledge.document_list_agent import list_all_documents
        answer = list_all_documents(folder_path=folder_path) or "No documents found."
        # When the folder contains exactly one file, auto-select it as last_file so
        # follow-up questions ("summarize this file", "what is performance?") have an
        # immediate file target without requiring the user to restate the filename.
        if folder_path and os.path.isdir(folder_path):
            try:
                _entries = [
                    e for e in os.listdir(folder_path)
                    if os.path.isfile(os.path.join(folder_path, e))
                ]
                if len(_entries) == 1:
                    _mem = _get_memory()
                    if _mem is not None:
                        _mem.set_last_file(_entries[0])
                        _mem.set_last_folder(folder_path)
                        log.info(
                            "Auto-selected single file as last_file=%r in folder=%r",
                            _entries[0], folder_path,
                        )
                elif _entries:
                    # Multi-file folder: at minimum track the folder so follow-ups
                    # like 'summarize the above file' are scoped to the right place.
                    _mem = _get_memory()
                    if _mem is not None:
                        try:
                            _mem.set_last_folder(folder_path)
                        except Exception:
                            pass
            except Exception as _exc:
                log.debug("_handle_document_list auto-select failed: %s", _exc)
        return AgentResponse(answer=answer)

    def _handle_combined_folder_file(
        self,
        text: str,
        folder_path: str,
        file_name: str,
    ) -> AgentResponse:
        """Execute a folder-summary and a file-explain simultaneously and combine.

        Called when a single query contains both a folder-level verb
        (summarize / list-all) and a file-explain verb (explain / describe /
        analyze) alongside a specific filename.
        """
        _exec = _get_tool_executor()
        if _exec is None:
            return AgentResponse(answer="Service unavailable.", intent="MULTI_INTENT")

        # 1. Folder-level summary
        folder_summary: Optional[str] = None
        try:
            tr = _exec.execute("documents.summarize", text, folder_path=folder_path)
            if tr and tr.success and tr.output:
                folder_summary = tr.output
        except Exception as exc:
            log.debug("_handle_combined_folder_file: folder summary error: %s", exc)

        # 2. File-level explanation
        file_result: Optional[str] = None
        file_source: Optional[str] = None
        try:
            tr = _exec.execute(
                "documents.search",
                f"explain {file_name}",
                folder_path=folder_path,
            )
            if tr and tr.success and tr.output:
                file_result = tr.output
                file_source = tr.source
        except Exception as exc:
            log.debug("_handle_combined_folder_file: file explain error: %s", exc)

        parts: list[str] = []
        if folder_summary:
            parts.append(folder_summary)
        if file_result:
            parts.append(f"**File Details ({file_name}):**\n{file_result}")
        combined = (
            "\n\n".join(parts)
            if parts
            else f"No information found in '{folder_path}'."
        )
        return AgentResponse(
            answer=combined,
            intent="MULTI_INTENT",
            source=file_source,
        )

    def _handle_retrieval(self, text: str, folder_path: Optional[str] = None) -> AgentResponse:
        from agents.knowledge.retrieval_agent import handle_retrieval, _is_summary_intent
        db = self._get_vector_db()
        win_docs_db = self._get_win_docs_db()
        extra = [win_docs_db] if win_docs_db is not None else []
        if db is None and not extra:
            from agents.core.general_agent import handle_general
            answer = handle_general(text, settings.model_name)
            return AgentResponse(
                answer=answer or "Knowledge base is still loading. Please try again shortly."
            )
        memory = _get_memory()
        last_file = memory.get_last_file() if memory else None
        answer, source = handle_retrieval(
            text, db, settings.retrieval_threshold, settings.model_name,
            extra_dbs=extra,
            last_file=last_file,
            folder_path=folder_path,
        )
        bullets = []
        if answer and not _is_summary_intent(text):
            bullets = _to_bullets(answer)
        return AgentResponse(
            answer=answer or "",
            source=source,
            bullets=bullets,
        )

    def _handle_file_qa(self, file_path: str, query: str) -> AgentResponse:
        """Answer *query* by loading *file_path* directly from disk.

        Does NOT rely on the vector store — works even if the file has never
        been indexed.  Used for follow-up questions when a specific file is
        already known from session memory (last_file + last_folder).

        Image handling
        --------------
        For .png / .jpg / .jpeg / .webp files the pipeline is:
          1. OCR (pytesseract)  — best for screenshots, scanned text.
          2. Vision model (llava/…) — best for diagrams, flowcharts, photos.
          3. Structured fallback — installation advice when both fail.

        Content sentinels from ``_load_document_from_path``:
          - ``"__VISION__:<text>"``         → vision description already generated
          - ``"__IMAGE_NO_TEXT__:<fname>"`` → no content could be extracted

        Flow: load file → (sentinel handling | pattern-extract / line-filter)
          → LLM → structured fallback.  Never returns a raw content dump.
        """
        from agents.knowledge.retrieval_agent import (
            _load_document_from_path,
            _is_summary_intent,
            answer_from_file,
        )
        if not os.path.isfile(file_path):
            return AgentResponse(
                answer=f"File not found:\n\U0001f4c1 {file_path}",
                intent="FILE_QA",
            )

        content = _load_document_from_path(file_path)
        fname = os.path.basename(file_path)
        log.info("[FILE_QA] Loaded %d chars from %r", len(content) if content else 0, file_path)

        # ── Image sentinel: vision model already produced a description ───────
        if isinstance(content, str) and content.startswith("__VISION__:"):
            vision_text = content[len("__VISION__:"):]
            # The vision text *is* the answer — no further LLM pass needed unless
            # the user asked a specific question (not just "summarize").
            is_summary = _is_summary_intent(query)
            if is_summary or not query.strip():
                return AgentResponse(
                    answer=f"\U0001f4f7 **{fname}**\n\n{vision_text}",
                    intent="FILE_QA",
                    source=file_path,
                )
            # Specific question — pass the vision description through the LLM
            answer = answer_from_file(
                query,
                vision_text,
                model_name=settings.model_name,
                file_path_used=file_path,
                is_summary=False
            )
            return AgentResponse(answer=answer, intent="FILE_QA", source=file_path)

        # ── Image sentinel: no content could be extracted at all ──────────────
        if isinstance(content, str) and content.startswith("__IMAGE_NO_TEXT__:"):
            ext = os.path.splitext(file_path)[1].lower()
            return AgentResponse(
                answer=(
                    f"\U0001f4f7 **{fname}** is an image file.\n\n"
                    "No text could be extracted from this image. Possible reasons:\n"
                    "- The image contains no readable text (e.g. photo, illustration).\n"
                    "- OCR (pytesseract) is not installed.\n"
                    "- No vision model (e.g. **llava**) is available in Ollama.\n\n"
                    "To enable image understanding:\n"
                    "  `pip install pytesseract pillow`  and install Tesseract-OCR, **or**\n"
                    "  `ollama pull llava`  to use a vision model."
                ),
                intent="FILE_QA",
                source=file_path,
            )

        # ── Standard text content ─────────────────────────────────────────────
        if not content:
            return AgentResponse(
                answer=(
                    f"\u26a0\ufe0f The file **{fname}** could not be read \u2014 it may be empty, "
                    "locked, or in an unsupported format. Please check the file and try again."
                ),
                intent="FILE_QA",
                source=file_path,
            )



        is_summary = _is_summary_intent(query)
        answer = answer_from_file(
            query,
            content,
            model_name=settings.model_name,
            file_path_used=file_path,
            is_summary=is_summary,
        )
        return AgentResponse(
            answer=answer,
            intent="FILE_QA",
            source=file_path,
        )

    def _handle_summary(self, folder_path: Optional[str] = None) -> AgentResponse:
        from agents.knowledge.summary_agent import handle_summary
        from services.document_service import document_service
        docs = document_service.get_documents()
        # Scope to active folder when set (e.g. follow-up after listing files)
        if folder_path and docs:
            folder_fp = os.path.normcase(os.path.normpath(folder_path))
            folder_fp_slash = folder_fp.replace("\\", "/").strip("/")
            folder_basename = os.path.basename(os.path.normpath(folder_path)).lower()

            def _in_folder(src: str) -> bool:
                s = os.path.normcase(os.path.normpath(src)).replace("\\", "/").strip("/")
                if s.startswith(folder_fp_slash):
                    return True
                return folder_basename in s.split("/")

            scoped = [d for d in docs if _in_folder(d.metadata.get("source", ""))]
            if scoped:
                docs = scoped
            else:
                return AgentResponse(
                    answer=(
                        f"No files found in:\n\U0001f4c1 {folder_path}\n\n"
                        "Possible reasons:\n"
                        "- Folder is empty or contains unsupported file types\n"
                        "- Files have not been indexed yet"
                    )
                )
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

    # ── AUDIO ────────────────────────────────────────────────────────────────

    def _handle_audio_transcribe(self, text: str) -> AgentResponse:
        from agents.knowledge.audio_agent import handle_audio_transcription
        answer = handle_audio_transcription(text)
        return AgentResponse(answer=answer or "", intent="AUDIO_TRANSCRIBE")

    def _handle_audio_query(self, text: str) -> AgentResponse:
        from agents.knowledge.audio_agent import handle_audio_query
        answer = handle_audio_query(text)
        return AgentResponse(answer=answer or "", intent="AUDIO_QUERY")

    def _handle_audio_list(self) -> AgentResponse:
        from agents.knowledge.audio_agent import handle_audio_list
        answer = handle_audio_list("")
        return AgentResponse(answer=answer or "", intent="AUDIO_LIST")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get_vector_db(self):
        try:
            from services.vector_store_service import vector_store_service
            if vector_store_service.is_ready:
                return vector_store_service.get_vector_db()
        except Exception:
            pass
        return None

    def _get_win_docs_db(self):
        """Return the Windows-docs ChromaDB instance, or None if not ready."""
        try:
            from services.document_indexer_service import document_indexer_service
            return document_indexer_service.get_vector_db()  # None until indexer finishes
        except Exception:
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
# Context follow-up helpers
# ---------------------------------------------------------------------------

# Words/phrases that indicate the user is referring back to something already
# discussed rather than introducing a new topic.
_FOLLOWUP_RE = re.compile(
    r"\b("
    r"this|that|it|its|the same|above|previous|last|the image|the file|the document"
    r"|the pdf|the screenshot|the picture|the report"
    r"|above file|above document|this file|this document|that file|that document"
    r"|the above file|the above document|the previous file|the previous document"
    r"|what does it|what did it|tell me more|more about|more details?|more info"
    r"|what else|anything else"
    r")\b",
    re.IGNORECASE,
)

# Pattern to detect an explicit filename in the query (any common extension)
_FILENAME_RE = re.compile(
    r"\b[\w][\w\-\. ]*\.(?:pdf|pptx|docx|txt|md|csv|xlsx|json|png|jpg|jpeg|webp)\b",
    re.IGNORECASE,
)


def _is_context_followup(text: str) -> bool:
    """Return True when the query appears to reference a prior turn's subject."""
    return bool(_FOLLOWUP_RE.search(text))


def _no_filename_in_query(text: str) -> bool:
    """Return True when *text* contains no explicit filename with an extension."""
    return not bool(_FILENAME_RE.search(text))


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

orchestrator = Orchestrator()
