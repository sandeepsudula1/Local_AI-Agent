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
# Tool name constants
# ---------------------------------------------------------------------------

_TOOL_DOC_SEARCH = "documents.search"

# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------

def _handle_document_search(user_input: str, **ctx) -> tuple[str, str]:
    """Delegate to the registered documents.search tool (carries vector_db + extra_dbs)."""
    from core.tool_registry import tool_registry
    # folder_path passed explicitly from orchestrator for ALLOW_FOLDER queries
    folder_path: Optional[str] = ctx.get("folder_path")
    # Pull last_file from memory only when NOT in folder scope
    # (folder scope supersedes file restriction)
    last_file: Optional[str] = None
    if not folder_path:
        try:
            from memory.conversation_memory import conversation_memory
            last_file = conversation_memory.get_last_file()
        except Exception:
            pass
    try:
        result = tool_registry.call(
            _TOOL_DOC_SEARCH,
            query=user_input,
            last_file=last_file,
            folder_path=folder_path,
        )
    except KeyError:
        log.warning(
            "documents.search not yet registered — vector store still loading; "
            "query=%r", user_input[:60],
        )
        return "The document index is still loading. Please try again in a moment.", ""
    if isinstance(result, tuple) and len(result) == 2:
        answer, source = result
        return (answer or ""), (source or "")
    return (str(result) if result else ""), ""


def _handle_document_summarize(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.summary_agent import handle_summary
    from services.document_service import document_service
    from configs.settings import settings
    import os as _os

    docs = document_service.get_documents()
    if not docs:
        return "No documents are available to summarize.", ""
    # Filter to folder scope when provided
    folder_path: Optional[str] = ctx.get("folder_path")
    if folder_path:
        folder_fp = _os.path.normcase(_os.path.normpath(folder_path))
        folder_fp_slash = folder_fp.replace("\\", "/").strip("/")
        folder_basename = _os.path.basename(_os.path.normpath(folder_path)).lower()

        def _src_in_folder(src: str) -> bool:
            s = _os.path.normcase(_os.path.normpath(src)).replace("\\", "/").strip("/")
            if s.startswith(folder_fp_slash):
                return True
            # Exact path-component basename match (no substring-in-name leak)
            if folder_basename and folder_basename in s.split("/"):
                return True
            return False

        docs = [d for d in docs if _src_in_folder(d.metadata.get("source", ""))]
        if not docs:
            return (
                f"No files found in:\n\U0001f4c1 {folder_path}\n\n"
                "Possible reasons:\n"
                "- Folder is empty or contains unsupported file types\n"
                "- Files have not been indexed yet"
            ), ""
    answer = handle_summary(docs, settings.model_name)
    return answer, ""


def _handle_document_list(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.document_list_agent import list_all_documents
    folder_path = ctx.get("folder_path") or None
    answer = list_all_documents(folder_path=folder_path)
    return answer, ""


def _handle_document_topics(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.topic_agent import handle_topic
    answer = handle_topic(user_input)
    return answer, ""


def _handle_email_search(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.email_query_agent import (
        handle_email_query,
        load_all_emails,
        improved_search_emails,
    )
    from memory.conversation_memory import conversation_memory

    answer = handle_email_query(user_input)

    # Store search results in conversation memory for context-aware replies
    # This allows "reply to first email", "reply to that email", etc.
    try:
        all_emails = load_all_emails()
        if all_emails:
            # Try improved email search
            try:
                results = improved_search_emails(user_input, max_results=20, use_semantic=True)
                if results:
                    conversation_memory.set_last_email_search_results(results)
                    log.info("[DEBUG] EMAIL_SEARCH: Stored %d search results in memory", len(results))
                    # Store first email for follow-up context (for EMAIL_REPLY follow-ups)
                    if results:
                        first_email = results[0]
                        conversation_memory.set_last_email(first_email)
                        ctx["_last_email"] = first_email
                        log.info("[DEBUG] EMAIL_SEARCH: Stored last_email in memory (from: %s, subject: %s)", 
                                first_email.get("from", "?"), first_email.get("subject", "?"))
                        # Verify it was stored
                        verify = conversation_memory.get_last_email()
                        if verify:
                            log.info("[DEBUG] EMAIL_SEARCH: Verified last_email in memory: from=%s", verify.get("from", "?"))
                        else:
                            log.warning("[DEBUG] EMAIL_SEARCH: Failed to verify last_email in memory!")
            except Exception as e:
                # Fallback: store all emails for context
                log.info("[DEBUG] EMAIL_SEARCH: Search failed (%s), using fallback", str(e)[:50])
                recent = all_emails[-20:]
                conversation_memory.set_last_email_search_results(recent)
                log.info("[DEBUG] EMAIL_SEARCH: Stored %d recent emails in memory (fallback)", len(recent))
                if recent:
                    first_email = recent[0]
                    conversation_memory.set_last_email(first_email)
                    ctx["_last_email"] = first_email
                    log.info("[DEBUG] EMAIL_SEARCH: Stored last_email from fallback: from=%s", first_email.get("from", "?"))
    except Exception as e:
        log.warning("[DEBUG] EMAIL_SEARCH: Could not store email search results: %s", e)

    return answer, ""


def _handle_email_summarize(user_input: str, **ctx) -> tuple[str, str]:
    from agents.knowledge.email_summarizer_agent import handle_email_summarizer
    answer = handle_email_summarizer(user_input)
    return answer, ""


def _handle_email_reply(user_input: str, **ctx) -> tuple[str, str]:
    """Generate a draft email reply (does not send ever).
    
    CONTEXT-AWARE: Uses last email from search results or memory, asks for clarification if ambiguous.
    Never sends automatically - always creates draft first.
    """
    from agents.knowledge.email_reply_agent_v2 import (
        find_target_email,
        generate_email_reply,
        get_tone_options,
    )
    from memory.conversation_memory import conversation_memory

    log.info("[DEBUG] EMAIL_REPLY: Handling reply for: %s", user_input[:60])

    # Parse tone from query
    tone = "professional"  # default
    tone_options = get_tone_options()

    user_lower = user_input.lower()
    for tone_name in tone_options.keys():
        if tone_name in user_lower:
            tone = tone_name
            break

    # Get last email search results from conversation memory
    search_results = conversation_memory.get_last_email_search_results()
    log.info("[DEBUG] EMAIL_REPLY: Retrieved %d search results from memory", len(search_results) if search_results else 0)

    # Find the target email using intelligent selection logic
    target_email = find_target_email(user_input, search_results=search_results)
    log.info("[DEBUG] EMAIL_REPLY: Explicit target found: %s", target_email.get("from") if target_email else None)

    # If no explicit target found, try last email from memory or search results
    if not target_email:
        # Priority 1: Try last email from memory (previous EMAIL_SEARCH or EMAIL_REPLY)
        try:
            last_email = conversation_memory.get_last_email()
            if last_email:
                log.info("[DEBUG] EMAIL_REPLY: Using last email from memory (from: %s)", last_email.get("from", "?"))
                target_email = last_email
            else:
                log.info("[DEBUG] EMAIL_REPLY: No last_email in memory")
        except Exception as e:
            log.warning("[DEBUG] EMAIL_REPLY: Error retrieving last_email from memory: %s", e)
    
    # Priority 2: Use first email from search results (if any)
    if not target_email and search_results:
        log.info("[DEBUG] EMAIL_REPLY: Using first email from search results (from: %s)", search_results[0].get("from", "?"))
        target_email = search_results[0]

    if not target_email:
        # Provide helpful error message
        log.warning("[DEBUG] EMAIL_REPLY: No email found to reply to! search_results=%d, memory_last_email=%s", 
                   len(search_results) if search_results else 0,
                   "exists" if conversation_memory.get_last_email() else "none")
        return (
            "❌ No email found to reply to.\n\n"
            "To reply to an email, try:\n"
            "  1. Search for emails first: 'search emails from alice'\n"
            "  2. Then reply: 'reply to that' or 'respond to the first one'\n"
            "  3. Or specify directly: 'reply to email from alice@company.com'\n\n"
            "I'll create a draft that you can review before sending.",
            ""
        )

    # Generate reply with the target email
    reply_text = generate_email_reply(target_email, tone=tone)

    if not reply_text:
        return (
            f"❌ Failed to generate reply for email: {target_email.get('subject', 'Unknown')}\n\n"
            "This might indicate an issue with the email content or LLM.",
            ""
        )

    # Extract email details
    from_addr = target_email.get("from", "Unknown")
    subject = target_email.get("subject", "(No Subject)")
    email_id = target_email.get("id", "?")

    log.info("[DEBUG] EMAIL_REPLY: Creating draft reply (to: %s, subject: %s)", from_addr, subject)

    from services.gmail_service import gmail_service
    from services.gmail_service import _CREDENTIALS_FILE as _GMAIL_CREDS_PATH
    from services.gmail_service import _TOKEN_FILE as _GMAIL_TOKEN_PATH

    gmail_result: dict = {}
    draft_response: dict = {}

    # ── Diagnostic: log paths and availability before any attempt ─────────
    log.info(
        "[GMAIL] Runtime check — credentials.json path : %s  exists=%s",
        _GMAIL_CREDS_PATH,
        _GMAIL_CREDS_PATH.exists(),
    )
    log.info(
        "[GMAIL] Runtime check — gmail_token.json path : %s  exists=%s",
        _GMAIL_TOKEN_PATH,
        _GMAIL_TOKEN_PATH.exists(),
    )
    _gmail_available = gmail_service.is_available()
    log.info("[GMAIL] is_available() returned: %s", _gmail_available)

    # ── Step 1: Attempt Gmail draft (primary) ─────────────────────────────
    if _gmail_available:
        log.info("[GMAIL] Attempting draft creation (to=%s  subject=Re: %s)", from_addr, subject)
        gmail_result = gmail_service.create_draft(
            to=from_addr,
            subject=f"Re: {subject}",
            body=reply_text,
        )
        log.info("[GMAIL] Full gmail_result: %s", gmail_result)
        if gmail_result.get("success"):
            log.info(
                "[GMAIL] Draft created successfully (id: %s  url: %s)",
                gmail_result.get("draft_id", "?"),
                gmail_result.get("gmail_draft_url", "?"),
            )
        else:
            log.warning(
                "[GMAIL] Failed, falling back to local draft. error=%s",
                gmail_result.get("error", "unknown"),
            )
    else:
        log.warning(
            "[GMAIL] is_available()=False — skipping Gmail, using local draft. "
            "credentials.json missing at: %s",
            _GMAIL_CREDS_PATH,
        )

    # ── Step 2: Local draft — only when Gmail unavailable or failed ────────
    if not gmail_result.get("success"):
        from services.draft_manager import draft_manager
        draft_response = draft_manager.create_draft(
            to=from_addr,
            subject=f"Re: {subject}",
            body=reply_text,
            reply_to_email_id=str(email_id),
            tone=tone,
            gmail_draft_id=None,
            gmail_draft_url=None,
        )
        log.info("[DEBUG] EMAIL_REPLY: Local draft saved (id: %s)", draft_response.get("draft_id", "?"))

    # Format display response
    local_draft_id = draft_response.get("draft_id", "")

    if gmail_result.get("success"):
        gmail_status_line = (
            f"✅ Draft saved to Gmail Drafts folder\n"
            f"🔗 Open draft: {gmail_result.get('gmail_draft_url', 'https://mail.google.com/mail/u/0/#drafts')}"
        )
        draft_id_line = f"📋 Gmail Draft ID: {gmail_result.get('draft_id', '?')} | Tone: {tone.capitalize()}"
    elif _gmail_available:
        # Gmail was reachable but the API call failed
        gmail_status_line = (
            f"⚠️  Gmail draft failed: {gmail_result.get('error', 'unknown error')}\n"
            f"📁 Draft saved locally as fallback (ID: {local_draft_id})"
        )
        draft_id_line = f"📋 Local Draft ID: {local_draft_id} | Tone: {tone.capitalize()}"
    else:
        # credentials.json absent or libraries missing
        gmail_status_line = (
            f"📁 Draft saved locally (Gmail API not configured)\n"
            f"   credentials.json expected at: {_GMAIL_CREDS_PATH}"
        )
        draft_id_line = f"📋 Local Draft ID: {local_draft_id} | Tone: {tone.capitalize()}"

    result = f"""📧 DRAFT REPLY ({tone.title()} Tone)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Original Email:
From: {from_addr}
Subject: {subject}
Date: {target_email.get('date', 'Unknown')}

Your Reply:
{reply_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{gmail_status_line}
{draft_id_line}
💡 Next steps: review, or say "send the reply" to send it
⚠️  Email will NOT be sent until you confirm"""

    # Also store in context for backward compatibility
    ctx["_draft_reply"] = {
        "draft_id": local_draft_id or gmail_result.get("draft_id", ""),
        "email_id": str(email_id),
        "to": from_addr,
        "subject": f"Re: {subject}",
        "body": reply_text,
        "tone": tone,
        "original_subject": subject,
        "gmail_draft_id": gmail_result.get("draft_id") if gmail_result.get("success") else None,
        "gmail_draft_url": gmail_result.get("gmail_draft_url") if gmail_result.get("success") else None,
    }
    # Store last email for potential follow-up
    ctx["_last_email"] = target_email
    conversation_memory.set_last_email(target_email)

    return result, ""


def _format_email_options(emails: list[dict]) -> str:
    """Format email list for user selection."""
    lines = []
    for i, email in enumerate(emails, 1):
        subject = email.get("subject", "(No Subject)")[:50]
        from_addr = email.get("from", "Unknown")[:40]
        lines.append(f"  {i}. From: {from_addr} | Subject: {subject}")
    return "\n".join(lines)


def _handle_email_send(user_input: str, **ctx) -> tuple[str, str]:
    """Send an email (requires explicit user confirmation).
    
    CRITICAL: Never sends without explicit confirmation keywords.
    Prioritizes draft_manager first, then falls back to context draft.
    """
    from services.email_send_service import (
        send_email_confirmation,
        send_email,
        get_email_from_config,
    )
    from services.draft_manager import draft_manager

    log.debug("Handling EMAIL_SEND: %s", user_input[:80])

    # Priority 1: Get draft from draft_manager (most reliable)
    draft_obj = draft_manager.get_latest_draft()
    
    # Priority 2: Fallback to context draft (backward compat)
    if not draft_obj:
        draft = ctx.get("_draft_reply")
        if not draft:
            return (
                "❌ No draft email to send.\n\n"
                "First generate a reply:\n"
                "  • 'search for emails from alice'\n"
                "  • 'reply to the first one'\n"
                "  Then: 'send it'",
                ""
            )
        # Convert context draft to simple dict for compatibility
        to_email = draft.get("to", "")
        subject = draft.get("subject", "")
        body = draft.get("body", "")
        draft_id = draft.get("draft_id")
    else:
        # Use draft from draft manager
        to_email = draft_obj.to
        subject = draft_obj.subject
        body = draft_obj.body
        draft_id = draft_obj.draft_id

    if not to_email:
        return "❌ No recipient email address found in draft", ""

    # CRITICAL: User must confirm with confirmation keywords
    # "yes", "send", "confirm", "go", "ok", etc.
    confirm_words = {"yes", "go", "send", "confirm", "proceed", "do it", "ok", "yeah", "sure", "yep"}
    user_lower = user_input.lower()

    has_confirm = any(word in user_lower for word in confirm_words)
    if not has_confirm:
        # Show confirmation prompt with draft preview
        confirm_msg = send_email_confirmation(to_email, subject, body)
        return (
            f"{confirm_msg}\n\n"
            "⚠️  Please confirm by saying 'yes', 'send it', 'confirm', or 'go ahead'",
            "",
        )

    # User confirmed - attempt to send
    success, message = send_email(to_email, subject, body, confirm=True)

    # Update draft status in draft manager if available
    if draft_obj and draft_id:
        if success:
            draft_manager.mark_draft_sent(draft_id, error_message=None)
        else:
            draft_manager.mark_draft_sent(draft_id, error_message=message)

    if success:
        result = f"✓ Email sent successfully!\n\n{message}"
        if draft_id:
            result += f"\n📋 Draft ID: {draft_id} | Status: sent"
        return (result, "")
    else:
        error_result = f"❌ Failed to send email\n\n{message}"
        if draft_id:
            error_result += f"\n📋 Draft ID: {draft_id} | Status: failed (can retry)"
        return (error_result, "")


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
    # Comparison reuses the same registered retrieval tool (which has vector_db + extra_dbs)
    from core.tool_registry import tool_registry
    try:
        result = tool_registry.call(_TOOL_DOC_SEARCH, query=user_input)
    except KeyError:
        log.warning(
            "documents.search not yet registered — vector store still loading; "
            "query=%r", user_input[:60],
        )
        return "The document index is still loading. Please try again in a moment.", ""
    if isinstance(result, tuple) and len(result) == 2:
        answer, source = result
        return (answer or ""), (source or "")
    return (str(result) if result else ""), ""


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    _TOOL_DOC_SEARCH:       _handle_document_search,
    "documents.summarize":  _handle_document_summarize,
    "documents.list":       _handle_document_list,
    "documents.topics":     _handle_document_topics,
    "email.search":         _handle_email_search,
    "email.summarize":      _handle_email_summarize,
    "email.reply":          _handle_email_reply,
    "email.send":           _handle_email_send,
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
