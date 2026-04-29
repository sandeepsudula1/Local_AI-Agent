"""
agent_mcp/chat.py
=================
Interactive MCP chat — type questions in plain English, see which MCP
tool is called behind the scenes, and get the answer back.

Run:
    cd "C:\Project\Local_AI Agent\local_ai_assistant"
    .\\venv311\\Scripts\\python.exe -m agent_mcp.chat

Every response shows:
    [MCP TOOL]  → which tool was selected
    [RESULT]    → the answer
"""

from __future__ import annotations

import sys
import os
import json

# ── project root on path ─────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── suppress noisy library output ────────────────────────────────────────
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import logging
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

# ── MCP layer ─────────────────────────────────────────────────────────────
from agent_mcp.bridge import MCPBridge
from agent_mcp.tools.system import system_intent, system_status

# ── background reminder poller ────────────────────────────────────────────
import threading
import time
import json

def _start_reminder_poller():
    """
    Polls data/reminders.json every 5 seconds.
    Fires a Windows toast notification for any reminder whose scheduled
    time has arrived and has not been fired yet.
    Mirrors the same logic as smart_agent.py's _reminder_poll_loop.
    """
    from agents.tasks.notification_agent import notify as _notify
    import dateparser as _dp

    try:
        from configs.settings import DATA_DIR as _DATA_DIR
        _rem_file = os.path.join(str(_DATA_DIR), "reminders.json")
    except Exception:
        _rem_file = os.path.join(_ROOT, "data", "reminders.json")

    def _load():
        if os.path.exists(_rem_file):
            try:
                with open(_rem_file, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save(rems):
        try:
            with open(_rem_file, "w") as f:
                json.dump(rems, f, indent=4)
        except Exception:
            pass

    def _poll():
        from datetime import datetime
        while True:
            try:
                rems = _load()
                changed = False
                now = datetime.now()
                for r in rems:
                    if r.get("fired"):
                        continue
                    t = None
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                        try:
                            t = datetime.strptime(r["time"], fmt)
                            break
                        except Exception:
                            pass
                    if not t:
                        try:
                            t = _dp.parse(r["time"])
                        except Exception:
                            pass
                    if not t:
                        continue
                    diff = (now - t).total_seconds()
                    # Fire if within a 90-second window after scheduled time
                    if 0 <= diff <= 90:
                        txt = r.get("text", "Reminder")
                        # Show inline alert in the chat terminal
                        print(f"\n  {_M}{_B}🔔 REMINDER: {txt}{_R}\n", flush=True)
                        _notify("Reminder", txt)
                        r["fired"] = True
                        changed = True
                if changed:
                    _save(rems)
            except Exception:
                pass
            time.sleep(5)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    return t

# ── intent → tool name mapping (for display) ──────────────────────────────
_INTENT_TOOL_MAP = {
    "GREETING":         "system.chat",
    "TIME":             "system.chat",
    "DATE":             "system.chat",
    "REMINDER_SET":     "reminders.set",
    "REMINDER_LIST":    "reminders.list",
    "REMINDER_DELETE":  "reminders.delete",
    "EMAIL_SUMMARY":    "email.summarize",
    "EMAIL_SEARCH":     "email.search",
    "DOCUMENT_LIST":    "documents.list",
    "SUMMARY":          "documents.summarize",
    "TOPIC":            "documents.topics",
    "RETRIEVAL":        "documents.search",
    "AUDIO_TRANSCRIBE": "audio.transcribe",
    "AUDIO_QUERY":      "audio.query",
    "AUDIO_LIST":       "audio.list",
    "COMPARE":          "system.chat",
    "CHAT":             "system.chat",
    "GENERAL":          "system.chat",
}

# ── ANSI colours (Windows 10+ supports these) ─────────────────────────────
_R  = "\033[0m"       # reset
_B  = "\033[1m"       # bold
_C  = "\033[36m"      # cyan    — MCP tool label
_G  = "\033[32m"      # green   — intent label
_Y  = "\033[33m"      # yellow  — user prompt
_M  = "\033[35m"      # magenta — section headers
_DIM = "\033[2m"      # dim     — separators


def _sep(char="─", width=60):
    print(f"{_DIM}{char * width}{_R}")


def _banner():
    _sep("═")
    print(f"{_B}{_M}  Local AI Assistant  ·  MCP Chat Interface{_R}")
    print(f"{_DIM}  Every answer shows which MCP tool handled it{_R}")
    _sep("═")

    # Quick health check
    try:
        s = system_status()
        ollama = "✓ online" if s.get("ollama_available") else "✗ offline"
        vstore = "✓ ready"  if s.get("vector_store_ready") else "✗ not built"
        docs   = s.get("document_count", 0)
        rems   = s.get("reminders_count", 0)
        print(f"  Ollama   : {_G}{ollama}{_R}")
        print(f"  VectorDB : {_G}{vstore}{_R}")
        print(f"  Documents: {docs}  |  Reminders: {rems}")
        print(f"  Rem.poll : {_G}active (every 5 s){_R}")
    except Exception as e:
        print(f"  {_DIM}(health check skipped: {e}){_R}")

    _sep()
    print(f"  Type a question. Type {_B}help{_R} for examples. {_B}exit{_R} to quit.")
    _sep()
    print()


_HELP = f"""
{_B}Example questions:{_R}

  Reminders
    remind me to call Sarah at 3pm
    remind me to take medicine in 30 minutes
    list my reminders
    delete the reminder about water

  Email
    show me emails from GitHub
    summarise my inbox
    any emails about invoices?

  Documents
    what is the company revenue?
    summarise all my documents
    what topics are in my documents?
    list available files

  General
    what is 2 + 2?
    compare Python vs JavaScript
    tell me a joke
"""


def _format_answer(raw) -> str:
    """Turn a raw tool result (str or dict) into a clean display string."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        # Try common answer keys in priority order
        for key in ("answer", "reply", "summary", "message", "topics"):
            val = raw.get(key)
            if val and isinstance(val, str) and val.strip():
                src = raw.get("source", "")
                if src and key == "answer":
                    return f"{val.strip()}\n{_DIM}  Source: {src}{_R}"
                return val.strip()
        # Fallback: pretty-print the whole dict
        return json.dumps(raw, indent=2, default=str)
    return str(raw)


def run():
    # Enable ANSI on Windows
    if sys.platform == "win32":
        os.system("color")   # one-time call enables VT sequences in cmd/PS

    # Start reminder background poller — fires Windows toasts when due
    _start_reminder_poller()

    _banner()
    bridge = MCPBridge()

    while True:
        try:
            raw_input = input(f"{_Y}{_B}You:{_R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{_DIM}Goodbye.{_R}\n")
            break

        if not raw_input:
            continue

        low = raw_input.lower()

        if low in ("exit", "quit", "bye"):
            print(f"\n{_DIM}Goodbye.{_R}\n")
            break

        if low in ("help", "?", "commands"):
            print(_HELP)
            continue

        # ── Step 1: classify intent ────────────────────────────────────
        try:
            intent_result = system_intent(raw_input)
            intent      = intent_result.get("intent", "GENERAL")
            intent_desc = intent_result.get("description", "")
        except Exception as e:
            intent      = "GENERAL"
            intent_desc = "Classification failed"

        tool_name = _INTENT_TOOL_MAP.get(intent, "system.chat")

        # ── Step 2: show routing info ──────────────────────────────────
        print()
        print(f"  {_DIM}Intent  : {_G}{intent}{_R}{_DIM}  —  {intent_desc}{_R}")
        print(f"  {_DIM}MCP Tool: {_C}{_B}{tool_name}{_R}")
        _sep()

        # ── Step 3: dispatch through bridge ───────────────────────────
        try:
            result = bridge.dispatch(intent, raw_input, raw=False)
        except Exception as e:
            result = f"(Error running tool: {e})"

        answer = _format_answer(result) if result is not None else \
                 "(No result — try rephrasing)"

        # ── Step 4: display answer ─────────────────────────────────────
        print(f"{_B}Assistant:{_R}")
        for line in answer.splitlines():
            print(f"  {line}")
        print()
        _sep()
        print()


if __name__ == "__main__":
    run()
