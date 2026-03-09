"""
main.py
=======
Production entry point for the Local AI Assistant.

Replaces the monolithic ``smart_agent.py`` startup block with a clean
layered boot sequence:

  1. Logging configured
  2. Documents loaded by DocumentService
  3. VectorStoreService started (background thread)
  4. ReminderService started (background thread)
  5. Optional MCP server mode (--mcp / --mcp-sse flags)
  6. Interactive CLI loop using the Orchestrator

Run
---
  venv311\\Scripts\\python main.py           # interactive mode
  venv311\\Scripts\\python main.py --mcp     # stdio MCP server
  venv311\\Scripts\\python main.py --mcp-sse # SSE  MCP server
"""

from __future__ import annotations

import os
import sys
import shutil

# ── 0. Auto-clear __pycache__ so code changes take effect immediately ───────
_ROOT = os.path.dirname(os.path.abspath(__file__))
for _dp, _dirs, _ in os.walk(_ROOT):
    for _d in list(_dirs):
        if _d == "__pycache__":
            try:
                shutil.rmtree(os.path.join(_dp, _d))
            except Exception:
                pass
            _dirs.remove(_d)  # don't recurse into deleted dirs

# ── 1. Fix Python path (allow `python main.py` from any cwd) ────────────────
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── 2. Suppress noisy library output before heavy imports ───────────────────
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

# ── 3. Auto-relaunch with venv311 if imapclient is unavailable ───────────────
try:
    import imapclient as _imap_check  # noqa: F401
except ImportError:
    import subprocess as _sp
    _venv = os.path.join(_ROOT, "venv311", "Scripts", "python.exe")
    if os.path.exists(_venv):
        sys.exit(_sp.run([_venv] + sys.argv).returncode)
    print("ERROR: venv311\\Scripts\\python.exe not found.")
    sys.exit(1)

# ── 4. Now we can import our own modules ────────────────────────────────────
from core.logging_config import setup_logging, get_logger
from configs.settings import settings

setup_logging(level=settings.log_level, log_format=settings.log_format)
log = get_logger(__name__)

# ── 5. MCP server mode (exit before starting interactive loop) ───────────────
if "--mcp" in sys.argv or "--mcp-sse" in sys.argv:
    from agent_mcp.server import main as _mcp_main
    _transport = "sse" if "--mcp-sse" in sys.argv else "stdio"
    log.info("Starting MCP server — transport: %s", _transport)
    _mcp_main(transport=_transport)
    sys.exit(0)

# ── 6. Boot services ────────────────────────────────────────────────────────
from services.document_service import document_service
from services.vector_store_service import vector_store_service
from services.reminder_service import reminder_service

log.info("Loading documents…")
documents = document_service.load_all()
print(f"Loaded {len(documents)} document chunk(s).")

log.info("Starting vector store service…")
vector_store_service.start(documents=documents)

log.info("Starting reminder service…")
reminder_service.start()

# ── 7. Background email polling ──────────────────────────────────────────────
import threading, time

def _email_poll_loop() -> None:
    from agents.knowledge.email_query_agent import invalidate_email_cache
    from agents.tasks.email_agent import EmailAgent
    while True:
        time.sleep(60)
        try:
            invalidate_email_cache()
            agent = EmailAgent()
            if hasattr(agent, "fetch_recent_emails"):
                new_emails = agent.fetch_recent_emails(last_n=settings.email_fetch_count)
            else:
                new_emails = agent.fetch_unread_emails()
            if new_emails:
                agent.save_to_cache(new_emails)
        except Exception:
            pass

threading.Thread(target=_email_poll_loop, daemon=True, name="email-poller").start()

# Initial email sync
print("[Email] Syncing inbox…")
try:
    from agents.tasks.email_agent import EmailAgent as _EA
    from agents.knowledge.email_query_agent import invalidate_email_cache as _inv
    _inv()
    _ea = _EA()
    _new = _ea.fetch_recent_emails(last_n=settings.email_fetch_count) if hasattr(_ea, "fetch_recent_emails") else _ea.fetch_unread_emails()
    if _new:
        _ea.save_to_cache(_new)
except Exception as _exc:
    log.debug("Initial email sync failed: %s", _exc)

# ── 8. Import orchestrator + memory ─────────────────────────────────────────
from pipelines.orchestrator import orchestrator

# ── 9. Show remembered facts on startup ─────────────────────────────────────
try:
    from memory.conversation_memory import conversation_memory as _memory
    _facts_summary = _memory.facts_summary()
    if _facts_summary:
        print(f"\n[Memory] {_facts_summary}")
except Exception:
    _memory = None

print("\nSmart AI Multi-Agent System Ready.\n")
print("Examples:")
print("  - Which is better, Python or Java?")
print("  - Compare 'Node.js' vs 'Deno'")
print("  - How many employees in 2024?")
print("  - Remind me at 15:30 to call Alice")
print("  - What tools are available?")
print("  - Forget everything\n")

# ── 10. Interactive CLI loop ─────────────────────────────────────────────────
while True:
    try:
        user_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAssistant shutting down…")
        reminder_service.stop()
        break

    if not user_input:
        continue

    if user_input.lower() == "exit":
        print("Assistant shutting down…")
        reminder_service.stop()
        break

    # ── built-in special commands ─────────────────────────────────────────────
    _cmd = user_input.lower().strip(" .?!")

    if _cmd in ("what tools are available", "list tools", "show tools", "what can you do"):
        try:
            from tools.tool_registry import tool_catalog
            print("Assistant:\n" + tool_catalog.describe_all())
        except Exception as _e:
            print("Assistant: Tool registry unavailable.")
        print()
        continue

    if _cmd in ("forget everything", "clear memory", "reset memory", "forget me"):
        try:
            from memory.conversation_memory import conversation_memory as _mem
            _mem.clear()
            print("Assistant: Memory cleared. I've forgotten everything about you.\n")
        except Exception:
            print("Assistant: Memory could not be cleared.\n")
        continue

    if _cmd in ("what do you remember", "show memory", "what do you know about me"):
        try:
            from memory.conversation_memory import conversation_memory as _mem
            facts = _mem.list_facts()
            if facts:
                lines = "\n".join(f"  {k}: {v}" for k, v in facts.items())
                print(f"Assistant: Here's what I remember about you:\n{lines}\n")
            else:
                print("Assistant: I don't have any saved facts about you yet.\n")
        except Exception:
            print("Assistant: Memory unavailable.\n")
        continue

    response = orchestrator.run(user_input)
    print(f"[Intent: {response.intent}]")

    # ── REMINDER CONFIRM (two-step UX) ───────────────────────────────────────
    if response.answer.startswith("__CONFIRM_REMINDER__"):
        payload = response.answer.removeprefix("__CONFIRM_REMINDER__")
        rtext, rtime = payload.split("||", 1)
        print(
            f"Assistant: I parsed this reminder as:\n"
            f"  - Message: '{rtext}'\n"
            f"  - Time:    {rtime}\n"
            f"Do you want to save it? (yes/no)"
        )
        try:
            conf = input("You: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            conf = "no"
        if conf in ("y", "yes"):
            from agents.tasks.reminder_agent import add_reminder
            print("Assistant:", add_reminder(rtext, rtime), "\n")
        else:
            print("Assistant: Reminder canceled.\n")
        continue

    # ── REMINDER DELETE (prompt for which one) ───────────────────────────────
    if response.answer == "__PROMPT_REMINDER_DELETE__":
        print("Assistant: Which reminder should I delete?")
        try:
            to_delete = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            to_delete = ""
        if to_delete:
            from agents.tasks.reminder_agent import delete_reminder
            print("Assistant:", delete_reminder(to_delete), "\n")
        continue

    # ── Normal response ──────────────────────────────────────────────────────
    if response.bullets:
        print("Assistant:")
        for b in response.bullets:
            print(f"  - {b}")
        if response.source:
            print(f"  (Source: {response.source})")
    else:
        print("Assistant:", response.answer)
        if response.source:
            print(f"  (Source: {response.source})")

    print()
