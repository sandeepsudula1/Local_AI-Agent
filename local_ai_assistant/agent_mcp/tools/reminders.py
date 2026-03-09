"""
mcp/tools/reminders.py
======================
MCP tool wrappers for the reminder subsystem.

These functions are THIN WRAPPERS around the existing
agents/tasks/reminder_agent.py functions.  No reminder logic lives here —
all business logic stays in the original agent.

Exposed MCP tools
-----------------
  reminders.set    → set a new reminder from natural language
  reminders.list   → list all pending / past reminders
  reminders.delete → delete reminders matching a keyword
"""

from __future__ import annotations

import sys
import os

# ── ensure project root is importable ──────────────────────────────────────
_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # mcp/
_ROOT    = os.path.dirname(_MCP_DIR)                                       # project root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── import existing reminder agent (unchanged) ─────────────────────────────
from agents.tasks.reminder_agent import (
    extract_reminder_details,
    add_reminder,
    list_reminders,
    delete_reminder,
    handle_set_reminder,
)


# ══════════════════════════════════════════════════════════════════════════════
# Tool: reminders.set
# ══════════════════════════════════════════════════════════════════════════════
def reminders_set(query: str) -> dict:
    """
    Parse a natural-language reminder request and save it.

    Supports expressions such as:
      • "Remind me to call John at 15:30"
      • "Remind me to take medicine in 20 minutes"
      • "Set a reminder for tomorrow at 9 am to send the report"

    Parameters
    ----------
    query : str
        The full natural-language user utterance describing the reminder.

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,        # human-readable confirmation or error
          "reminder_text": str,  # parsed reminder text (or "" on failure)
          "reminder_time": str   # ISO-like datetime string (or "" on failure)
        }
    """
    if not query or not query.strip():
        return {
            "success": False,
            "message": "No reminder text provided.",
            "reminder_text": "",
            "reminder_time": "",
        }

    import re as _re

    # ── Explicit "yesterday" guard ────────────────────────────────────────
    # dateparser converts "yesterday at X" to a future time (today/tomorrow),
    # so catch it before parsing to give the user a clear error.
    if _re.search(r"\byesterday\b", query, _re.IGNORECASE):
        return {
            "success": False,
            "message": (
                "The requested time is in the past (yesterday).\n"
                "Did you mean today or a specific future time? "
                "Example: 'Remind me today at 5 PM' or 'Remind me tomorrow at 9 AM'."
            ),
            "reminder_text": "",
            "reminder_time": "",
        }

    # ── Invalid time format guard ─────────────────────────────────────────
    # Reject obviously impossible times like 25:00, 99:30, etc.
    _invalid_time = _re.search(r"\b([2-9]\d|1\d{2,})\s*:\s*\d{2}\b", query)
    if _invalid_time:
        return {
            "success": False,
            "message": (
                f"Invalid time '{_invalid_time.group()}'. "
                "Hours must be 0–23 and minutes 0–59. "
                "Example: 'Remind me at 14:30' or 'at 9 AM'."
            ),
            "reminder_text": "",
            "reminder_time": "",
        }

    # ── Past-time guard ───────────────────────────────────────────────────
    # Parse the time first so we can warn the user before saving a reminder
    # that is already in the past (e.g. "remind me yesterday at 5 PM").
    _preview_text, _preview_time = extract_reminder_details(query.strip())
    if _preview_time:
        try:
            from datetime import datetime, timedelta
            _parsed_dt = None
            for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    _parsed_dt = datetime.strptime(_preview_time, _fmt)
                    break
                except ValueError:
                    pass
            if _parsed_dt and _parsed_dt < datetime.now() - timedelta(minutes=5):
                _next = _parsed_dt + timedelta(days=1)
                return {
                    "success": False,
                    "message": (
                        f"The requested reminder time ({_preview_time}) is already in the past.\n"
                        f"Do you want me to schedule it for the next available time instead? "
                        f"(e.g. {_next.strftime('%Y-%m-%d %H:%M')})"
                    ),
                    "reminder_text": _preview_text or "",
                    "reminder_time": _preview_time,
                }
        except Exception:
            pass  # If check fails, fall through and let the normal flow handle it

    # handle_set_reminder supports multiple reminders separated by ';'
    success, msg = handle_set_reminder(query.strip())
    if success:
        # Re-parse to return structured fields for the first reminder
        text, rtime = extract_reminder_details(query.strip())
        return {
            "success": True,
            "message": msg,
            "reminder_text": text or "",
            "reminder_time": rtime or "",
        }

    # Fallback: try direct parse
    text, rtime = extract_reminder_details(query.strip())
    if rtime:
        result = add_reminder(text or "Reminder", rtime)
        return {
            "success": True,
            "message": result,
            "reminder_text": text or "Reminder",
            "reminder_time": rtime,
        }

    return {
        "success": False,
        "message": (
            "Could not parse a time from your request. "
            "Try: 'Remind me to … at HH:MM' or 'in N minutes'."
        ),
        "reminder_text": "",
        "reminder_time": "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: reminders.list
# ══════════════════════════════════════════════════════════════════════════════
def reminders_list() -> dict:
    """
    Return all reminders (pending and recently fired).

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,         # formatted text suitable for display
          "reminders": list[dict] # raw reminder objects from JSON store
        }

    Each reminder dict has the shape:
        {"text": str, "time": str, "fired": bool}
    """
    import json, os

    # Read raw data for the structured field
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _PROJ = os.path.dirname(os.path.dirname(_HERE))
    rem_file = os.path.join(_PROJ, "data", "reminders.json")

    raw: list[dict] = []
    if os.path.exists(rem_file):
        try:
            with open(rem_file, "r") as f:
                raw = json.load(f)
        except Exception:
            raw = []

    formatted = list_reminders()   # delegates to agent — returns pretty string
    return {
        "success": True,
        "message": formatted,
        "reminders": raw,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: reminders.delete
# ══════════════════════════════════════════════════════════════════════════════
def reminders_delete(keyword: str) -> dict:
    """
    Delete all reminders whose text contains *keyword* (case-insensitive).

    Parameters
    ----------
    keyword : str
        A word or phrase to match against reminder text.
        Example: "call John", "medicine", "report"

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str   # confirmation or error
        }
    """
    if not keyword or not keyword.strip():
        return {
            "success": False,
            "message": "Please provide a keyword to identify the reminder to delete.",
        }

    result = delete_reminder(keyword.strip())
    return {
        "success": True,
        "message": result,
    }
