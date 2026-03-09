"""
mcp/bridge.py
=============
MCPBridge — in-process adapter that lets smart_agent.py route user
requests through the MCP tool layer without starting a separate server.

Why this exists
---------------
smart_agent.py already contains a working intent → agent dispatch loop.
MCPBridge mirrors that routing but delegates each intent to the MCP tool
wrappers in mcp/tools/*.py instead of calling agents directly.

This means:
  • You get MCP-compatible structured output (dicts with success/error)
    for every response — useful for the API server or UI layers.
  • Future tool changes happen in ONE place (mcp/tools/).
  • smart_agent.py CLI continues to work unchanged.

Usage in smart_agent.py
-----------------------
    # Optional import — gracefully ignored if mcp package is missing
    try:
        from mcp.bridge import MCPBridge
        _mcp_bridge = MCPBridge()
        _MCP_ENABLED = True
    except ImportError:
        _MCP_ENABLED = False

    # Then in the main loop, after intent classification:
    if _MCP_ENABLED:
        result = _mcp_bridge.dispatch(intent, user_input)
        if result is not None:
            print("Assistant:", result)
            continue
    # ... existing fallback routing below ...

Standalone usage
----------------
    from mcp.bridge import MCPBridge
    bridge = MCPBridge()
    result = bridge.dispatch("REMINDER_SET", "remind me to call John at 3pm")
    print(result)
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta

# ── project root on path ───────────────────────────────────────────────────
_BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))   # mcp/
_ROOT       = os.path.dirname(_BRIDGE_DIR)                  # project root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── MCP tool implementations (lazy import per category) ────────────────────
# We import lazily so a broken optional dependency (e.g. OCR) does not
# prevent the bridge from loading at all.

def _import_reminders():
    from agent_mcp.tools.reminders import reminders_set, reminders_list, reminders_delete
    return reminders_set, reminders_list, reminders_delete

def _import_emails():
    from agent_mcp.tools.emails import email_search, email_summarize, email_list_all
    return email_search, email_summarize, email_list_all

def _import_documents():
    from agent_mcp.tools.documents import (
        documents_search, documents_summarize, documents_topics, documents_list,
    )
    return documents_search, documents_summarize, documents_topics, documents_list

def _import_system():
    from agent_mcp.tools.system import system_chat, system_intent, system_status
    return system_chat, system_intent, system_status

def _import_audio():
    from agent_mcp.tools.audio import audio_transcribe, audio_query, audio_list
    return audio_transcribe, audio_query, audio_list


# ══════════════════════════════════════════════════════════════════════════════
# MCPBridge
# ══════════════════════════════════════════════════════════════════════════════

class MCPBridge:
    """
    In-process MCP dispatcher.

    Maps planner_agent intent labels → mcp/tools/* calls and returns a
    formatted string ready for the smart_agent.py console output OR a
    raw tool-result dict when raw=True.
    """

    def dispatch(
        self,
        intent: str,
        user_input: str,
        raw: bool = False,
        vector_db=None,
    ) -> str | dict | None:
        """
        Route *user_input* to the correct MCP tool based on *intent*.

        Parameters
        ----------
        intent : str
            Intent label from planner_agent.decide_intent().
        user_input : str
            The original user message.
        raw : bool
            When True, return the raw tool-result dict.
            When False (default), return a formatted string for display.
        vector_db :
            Ignored here — the MCP document tools manage their own
            vector DB singleton.  Kept for API compatibility.

        Returns
        -------
        str | dict | None
            None if the intent is not handled by any MCP tool
            (smart_agent.py should use its own fallback in that case).
        """
        intent = (intent or "").strip().upper()

        # ── TIME / DATE (no tool needed — instant) ────────────────────────
        if intent == "TIME":
            t = datetime.now().strftime("%H:%M:%S")
            return {"success": True, "reply": t} if raw else t

        if intent == "DATE":
            d = datetime.now().strftime("%A, %d %B %Y")
            return {"success": True, "reply": d} if raw else d

        if intent == "GREETING":
            msg = "Hello! How can I help you today?"
            return {"success": True, "reply": msg} if raw else msg

        # ── REMINDERS ─────────────────────────────────────────────────────
        if intent == "REMINDER_SET":
            fn_set, _, _ = _import_reminders()
            result = fn_set(user_input)
            return result if raw else self._format_reminder_set(result)

        if intent == "REMINDER_LIST":
            _, fn_list, _ = _import_reminders()
            result = fn_list()
            return result if raw else result.get("message", "No reminders found.")

        if intent == "REMINDER_DELETE":
            # Extract keyword: everything after "delete"/"remove" etc.
            import re
            kw = re.sub(
                r"^.*(delete|remove|cancel|clear)\s+", "", user_input,
                flags=re.I,
            ).strip()
            if not kw:
                kw = user_input.strip()
            _, _, fn_del = _import_reminders()
            result = fn_del(kw)
            return result if raw else result.get("message", "Done.")

        # ── EMAIL ─────────────────────────────────────────────────────────
        if intent == "EMAIL_SUMMARY":
            _, fn_sum, _ = _import_emails()
            result = fn_sum(query="")
            return result if raw else result.get("summary", "No emails available.")

        if intent == "EMAIL_SEARCH":
            fn_srch, _, _ = _import_emails()
            result = fn_srch(user_input, max_results=8)
            return result if raw else result.get("summary", "No matching emails found.")

        # ── DOCUMENTS ─────────────────────────────────────────────────────
        if intent == "DOCUMENT_LIST":
            _, _, _, fn_lst = _import_documents()
            result = fn_lst()
            return result if raw else result.get("summary", "No documents found.")

        if intent == "RETRIEVAL":
            fn_srch, _, _, _ = _import_documents()
            result = fn_srch(user_input)
            if raw:
                return result
            if result.get("success") and result.get("answer"):
                src = result.get("source", "")
                ans = result["answer"]
                return f"{ans}\n(Source: {src})" if src else ans
            return "No relevant information found in the documents."

        if intent == "SUMMARY":
            import re as _re
            # If the user names a specific document/report, search it instead of
            # summarising all documents (e.g. "Summarize the internship report").
            _specific_doc = _re.search(
                r'\bthe\s+\w+\s+(report|document|file|data|notes?|plan|strategy|analysis)\b'
                r'|\w+\.(pdf|csv|docx?|xlsx?|txt)\b',
                user_input, _re.IGNORECASE,
            )
            if _specific_doc and not _re.search(r'\b(all|every|entire|both)\b', user_input, _re.IGNORECASE):
                fn_srch, _, _, _ = _import_documents()
                result = fn_srch(user_input)
                if raw:
                    return result
                if result.get("success") and result.get("answer"):
                    src = result.get("source", "")
                    ans = result["answer"]
                    return f"{ans}\n(Source: {src})" if src else ans
                return "No relevant information found in the documents."
            _, fn_sum, _, _ = _import_documents()
            result = fn_sum()
            return result if raw else result.get("summary", "Could not summarise.")

        if intent == "TOPIC":
            _, _, fn_top, _ = _import_documents()
            result = fn_top()
            return result if raw else result.get("topics", "Could not extract topics.")

        # ── AUDIO ─────────────────────────────────────────────────────────
        if intent == "AUDIO_TRANSCRIBE":
            import re as _re
            # Strip leading numbered-list prefix e.g. "1. Transcribe ..."
            _file_arg = _re.sub(r"^\d+\.\s*", "", user_input.strip()).strip()
            # Strip leading command verb
            _file_arg = _re.sub(
                r"^(transcribe|upload|index|add|process|convert|load)\s+",
                "",
                _file_arg,
                flags=_re.IGNORECASE,
            ).strip()
            # Strip leading noise qualifiers: "file", "the", "audio", "this", etc.
            _file_arg = _re.sub(
                r"^(this\s+|the\s+|audio\s+file\s+|audio\s+|voice\s+note\s+|"
                r"file\s+|following\s+|my\s+)+",
                "",
                _file_arg,
                flags=_re.IGNORECASE,
            ).strip()
            # Strip trailing noise: "audio file", "voice note", "file", "please"
            _file_arg = _re.sub(
                r"\s+(audio\s+file|voice\s+note|file|please)[\s.,]*$",
                "",
                _file_arg,
                flags=_re.IGNORECASE,
            ).strip()
            # If an audio extension is present, keep only the filename portion
            ext_match = _re.search(
                r'(.+\.(mp[34]|wav|m4a|flac|ogg|webm))',
                _file_arg,
                _re.IGNORECASE,
            )
            if ext_match:
                _file_arg = ext_match.group(1).strip()
            else:
                # No audio extension found — user didn't specify a filename
                _, _, fn_lst = _import_audio()
                lst = fn_lst()
                files_msg = lst.get("message", "")
                return (
                    "No audio filename was specified.\n"
                    "Please provide the filename, e.g.: 'Transcribe meeting.mp3'\n"
                    + (f"\n{files_msg}" if files_msg else "")
                )
            fn_tx, _, _ = _import_audio()
            result = fn_tx(_file_arg)
            if raw:
                return result
            if result.get("success"):
                fname    = result.get("filename", "")
                duration = result.get("duration", "")
                chunks   = result.get("chunks_stored", 0)
                preview  = result.get("transcript_preview", "")
                return (
                    f"Transcribed '{fname}' ({duration}) — {chunks} chunks indexed.\n"
                    f"Preview: {preview}"
                )
            return result.get("error", "Transcription failed.")

        if intent == "AUDIO_QUERY":
            _, fn_q, _ = _import_audio()
            result = fn_q(user_input)
            if raw:
                return result
            if result.get("success") and result.get("answer"):
                answer  = result["answer"]
                sources = result.get("sources", [])
                if sources:
                    ts_refs = ", ".join(
                        f"{s['filename']} [{s['start_ts']} → {s['end_ts']}]"
                        for s in sources[:3]
                    )
                    return f"{answer}\n\nSources: {ts_refs}"
                return answer
            return result.get("error", "No audio transcripts found.")

        if intent == "AUDIO_LIST":
            _, _, fn_lst = _import_audio()
            result = fn_lst()
            return result if raw else result.get("message", "No audio files indexed.")

        # ── CHAT / GENERAL (delegates to LLM) ─────────────────────────────
        if intent in ("CHAT", "GENERAL", "COMPARE"):
            fn_chat, _, _ = _import_system()
            result = fn_chat(user_input)
            return result if raw else result.get("reply", "")

        # Intent not handled here — caller should use its own routing
        return None

    # ── formatting helpers ─────────────────────────────────────────────────

    @staticmethod
    def _format_reminder_set(result: dict) -> str:
        if result.get("success"):
            text = result.get("reminder_text", "")
            rtime = result.get("reminder_time", "")
            lines = [result.get("message", "Reminder saved.")]
            if text:
                lines.append(f"  Message : {text}")
            if rtime:
                lines.append(f"  Time    : {rtime}")
            return "\n".join(lines)
        return result.get("message", "Could not set reminder.")

    def health(self) -> dict:
        """Return system status (delegates to system.status tool)."""
        _, _, fn_status = _import_system()
        return fn_status()
