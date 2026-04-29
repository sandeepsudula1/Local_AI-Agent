"""
mcp/tools/system.py
===================
MCP tool wrappers for general / system-level behaviour.

Wraps (without modifying):
  • agents/core/general_agent.py  → handle_general (LLM chat)
  • agents/core/planner_agent.py  → decide_intent  (intent classification)

Exposed MCP tools
-----------------
  system.chat    → send a free-form message to the local LLM
  system.intent  → classify the user's intent (mirrors smart_agent routing)
  system.status  → return server health / model availability
"""

from __future__ import annotations

import sys
import os
from datetime import datetime

# ── project root on path ───────────────────────────────────────────────────
_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT    = os.path.dirname(_MCP_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── existing agents (unchanged) ────────────────────────────────────────────
from agents.core.general_agent import handle_general
from agents.core.planner_agent import decide_intent

from configs.llm_config import MODEL_NAME
_DEFAULT_MODEL = "llama3"


# ══════════════════════════════════════════════════════════════════════════════
# Tool: system.chat
# ══════════════════════════════════════════════════════════════════════════════
def system_chat(
    message: str,
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.7,
) -> dict:
    """
    Send a natural-language message to the locally running LLM (Ollama).

    This is the "catch-all" tool — use it when no other specialised tool
    (reminders, emails, documents) is more appropriate.

    Parameters
    ----------
    message : str
        The user's message or question.
    model : str
        Ollama model name (default: mistral).
    temperature : float
        Creativity level 0.0 (factual) → 1.0 (creative). Default 0.7.

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,   # echo of the input
          "reply": str,     # LLM response
          "model": str
        }
    """
    if not message or not message.strip():
        return {
            "success": False,
            "message": message,
            "reply": "No message provided.",
            "model": model,
        }

    try:
        reply = handle_general(
            message.strip(),
            model or _DEFAULT_MODEL,
            temperature=float(temperature),
        )
        return {
            "success": True,
            "message": message.strip(),
            "reply": reply,
            "model": model or _DEFAULT_MODEL,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": message.strip(),
            "reply": f"LLM call failed: {exc}",
            "model": model or _DEFAULT_MODEL,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: system.intent
# ══════════════════════════════════════════════════════════════════════════════
def system_intent(message: str) -> dict:
    """
    Classify the intent of a user message using the same planner used by
    smart_agent.py.

    Useful for external clients that want to decide which tool to call
    before making the actual tool call.

    Parameters
    ----------
    message : str
        The raw user utterance to classify.

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,
          "intent": str,     # one of the intent labels (e.g. REMINDER_SET)
          "description": str # human-readable explanation of the intent
        }

    Intent labels
    -------------
    GREETING, TIME, DATE,
    REMINDER_SET, REMINDER_LIST, REMINDER_DELETE,
    EMAIL_SUMMARY, EMAIL_SEARCH,
    DOCUMENT_LIST, SUMMARY, TOPIC, RETRIEVAL,
    COMPARE, CHAT, GENERAL
    """
    _DESCRIPTIONS = {
        "GREETING":        "A greeting or social opener.",
        "TIME":            "User is asking for the current time.",
        "DATE":            "User is asking for today's or a relative date.",
        "REMINDER_SET":    "User wants to create a new reminder or alarm.",
        "REMINDER_LIST":   "User wants to see their existing reminders.",
        "REMINDER_DELETE": "User wants to delete a reminder.",
        "EMAIL_SUMMARY":   "User wants a summary of their inbox.",
        "EMAIL_SEARCH":    "User is searching for specific emails.",
        "DOCUMENT_LIST":   "User wants to know which documents are available.",
        "SUMMARY":         "User wants a summary of one or more documents.",
        "TOPIC":           "User wants to know the main topics in documents.",
        "RETRIEVAL":       "User has a specific question answered by documents.",
        "COMPARE":         "User wants to compare two concepts or items.",
        "CHAT":            "Pure conversational message — no tool needed.",
        "GENERAL":         "Uncategorised / unclear intent.",
    }

    if not message or not message.strip():
        return {
            "success": False,
            "message": message,
            "intent": "GENERAL",
            "description": "Empty message.",
        }

    try:
        intent = decide_intent(message.strip())
        return {
            "success": True,
            "message": message.strip(),
            "intent": intent,
            "description": _DESCRIPTIONS.get(intent, "Unknown intent."),
        }
    except Exception as exc:
        return {
            "success": False,
            "message": message.strip(),
            "intent": "GENERAL",
            "description": f"Intent classification failed: {exc}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: system.status
# ══════════════════════════════════════════════════════════════════════════════
def system_status() -> dict:
    """
    Return a health-check snapshot of the MCP server and its dependencies.

    Checks:
      • Ollama daemon availability
      • ChromaDB vector store presence
      • Documents folder file count
      • Reminders file presence

    Returns
    -------
    dict
        {
          "success": bool,
          "timestamp": str,
          "ollama_available": bool,
          "model": str,
          "vector_store_ready": bool,
          "document_count": int,
          "reminders_count": int
        }
    """
    import json

    # Ollama check
    ollama_ok = False
    try:
        import ollama
        ollama.list()
        ollama_ok = True
    except Exception:
        pass

    # Vector store check
    try:
        from configs.settings import DATA_DIR as _DATA_DIR
        _data_root = str(_DATA_DIR)
    except Exception:
        _data_root = _ROOT + "/data"
    vstore_ok = (
        os.path.exists(os.path.join(_data_root, "vector_store_v2"))
        and bool(list(os.scandir(os.path.join(_data_root, "vector_store_v2"))))
    )

    # Document count — only count actual user document extensions (mirrors documents.list)
    _DOC_EXTS = {'.pdf', '.csv', '.txt', '.docx', '.doc', '.xlsx', '.xls', '.png', '.jpg', '.jpeg'}
    doc_count = 0
    docs_path = os.path.join(_data_root, "documents")
    if os.path.exists(docs_path):
        doc_count = sum(
            1 for f in os.listdir(docs_path)
            if os.path.isfile(os.path.join(docs_path, f))
            and os.path.splitext(f)[1].lower() in _DOC_EXTS
        )

    # Reminders count
    rem_count = 0
    rem_path  = os.path.join(_data_root, "reminders.json")
    if os.path.exists(rem_path):
        try:
            rem_count = len(json.load(open(rem_path)))
        except Exception:
            pass

    return {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "ollama_available": ollama_ok,
        "model": "llama3",
        "vector_store_ready": vstore_ok,
        "document_count": doc_count,
        "reminders_count": rem_count,
    }
