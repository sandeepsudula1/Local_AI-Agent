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
    # NOTE: "yes", "no", "ok" etc. are intentionally excluded here so the
    # permission-request handler (step 0a) can intercept them first.
    _CONVERSATIONAL: frozenset[str] = frozenset({
        "thanks", "thank you", "alright", "bye", "got it", "cool", "nice",
    })

    def __init__(self) -> None:
        self._last_email_fetch: float = 0.0

    # ── main entry point ────────────────────────────────────────────────────

    def run(self, user_input: str) -> AgentResponse:  # noqa: C901
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
                    except Exception:
                        pass
                log.info("[ORCHESTRATOR] Folder resolved (ALLOW_FOLDER): %r", _forced_folder)
        except Exception as _ac_exc:
            log.debug("access_control check failed: %s", _ac_exc)

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
                    if _ORCH_REPLY_RE.search(text):
                        log.info(
                            "[ORCHESTRATOR] Email-context safety net: intent %s -> EMAIL_REPLY "
                            "(email_ctx=True, input=%r)",
                            intent, text[:50],
                        )
                        intent = "EMAIL_REPLY"
            except Exception as _oex:
                log.debug("Email-context safety net check failed: %s", _oex)

        # Safety override: when access control has already resolved a folder scope,
        # any non-document intent is almost certainly a misclassification.
        _DOCUMENT_INTENTS = {
            "RETRIEVAL", "DOCUMENT_SEARCH", "DOCUMENT_FOLDER_QUERY",
            "SUMMARY", "DOCUMENT_SUMMARY", "DOCUMENT_LIST", "TOPIC", "COMPARE",
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
                resp = self._dispatch(intent, enriched_text, memory=memory, folder_path=_forced_folder)
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

    def _post_process(
        self,
        user_input: str,
        resp: AgentResponse,
        tool_name: Optional[str] = None,
        tool_result=None,
    ) -> None:
        """Store conversation turn, update session context, and emit structured log."""
        memory = _get_memory()
        if memory is not None:
            try:
                memory.add_turn("user", user_input)
                if resp.answer:
                    memory.add_turn("assistant", resp.answer[:500])
            except Exception as exc:
                log.debug("memory.add_turn failed: %s", exc)

            # Update session context: last referenced file and last intent
            try:
                if resp.intent not in {"CHAT", "GREETING", "GENERAL", "TIME", "DATE", "EMPTY"}:
                    memory.set_last_intent(resp.intent)
                if resp.source:
                    # source may be a comma-separated list; take the first entry
                    first_source = resp.source.split(",")[0].strip()
                    if first_source:
                        memory.set_last_file(first_source)
                        log.debug("Memory: last_file updated to %r", first_source)
            except Exception as exc:
                log.debug("memory context update failed: %s", exc)

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

    def _dispatch(self, intent: str, text: str, memory=None, folder_path: Optional[str] = None) -> AgentResponse:  # noqa: C901
        match intent:
            case "CHAT":
                return self._handle_chat(text, memory=memory)
            case "GENERAL":
                return self._handle_general(text, memory=memory, folder_path=folder_path)
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
            case "AUDIO_TRANSCRIBE" | "TRANSCRIPTION":
                return self._handle_audio_transcribe(text)
            case "AUDIO_QUERY" | "AUDIO_SEARCH":
                return self._handle_audio_query(text)
            case "AUDIO_LIST":
                return self._handle_audio_list()
            case "DOCUMENT_LIST":
                return self._handle_document_list(folder_path=folder_path)
            case "RETRIEVAL" | "DOCUMENT_SEARCH" | "DOCUMENT_FOLDER_QUERY":
                return self._handle_retrieval(text, folder_path=folder_path)
            case "SUMMARY" | "DOCUMENT_SUMMARY":
                return self._handle_summary(folder_path=folder_path)
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

    def _handle_general(self, text: str, memory=None, folder_path: Optional[str] = None) -> AgentResponse:
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

        # ── Enrich with last_file for context follow-ups ─────────────────────
        enriched = text
        if memory is not None and _is_context_followup(text) and _no_filename_in_query(text):
            last_file = memory.get_last_file()
            if last_file:
                enriched = f"{text} {last_file}"
                log.info(
                    "GENERAL context follow-up — enriched query with last_file=%r",
                    last_file,
                )

        db = self._get_vector_db()
        win_docs_db = self._get_win_docs_db()
        extra = [win_docs_db] if win_docs_db is not None else []

        if db is not None or extra:
            try:
                from agents.knowledge.retrieval_agent import handle_retrieval
                _last_file = memory.get_last_file() if memory is not None else None
                ans, src = handle_retrieval(
                    enriched, db, settings.retrieval_threshold, settings.model_name,
                    extra_dbs=extra,
                    last_file=_last_file,
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
        try:
            from agents.knowledge.email_summarizer_agent import handle_email_summary
        except ImportError:
            return AgentResponse(answer="Email service is currently unavailable.")
        return AgentResponse(answer=handle_email_summary() or "No emails to summarise.")

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
        """
        from agents.knowledge.retrieval_agent import (
            _load_document_from_path,
            _is_summary_intent,
            _ask_llm,
        )
        if not os.path.isfile(file_path):
            return AgentResponse(
                answer=f"File not found:\n\U0001f4c1 {file_path}",
                intent="FILE_QA",
            )
        content = _load_document_from_path(file_path)
        if not content:
            # Unsupported type or read error — fall through to RAG
            return AgentResponse(answer="", intent="FILE_QA")
        is_summary = _is_summary_intent(query)
        answer = _ask_llm(
            settings.model_name,
            content,
            query,
            os.path.basename(file_path),
            is_summary=is_summary,
        )
        if not answer:
            answer = content[:3000]  # bare text fallback
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
