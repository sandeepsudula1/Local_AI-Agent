"""
mcp/server.py
=============
FastMCP server — exposes all local AI assistant capabilities as MCP tools
that any MCP-compatible LLM client can call (Claude Desktop, VS Code
Copilot extensions, custom clients, etc.).

Running the server
------------------
  # stdio transport (used by Claude Desktop / VS Code extensions)
  python -m mcp.server

  # SSE transport (HTTP-based — useful for remote or multi-client access)
  python -m mcp.server --transport sse --port 8765

  # Quick smoke-test (lists registered tools and exits)
  python -m mcp.server --list-tools

Architecture
------------
  MCP Client (Claude / VS Code / …)
       │  MCP protocol (stdio or SSE)
       ▼
  mcp/server.py          ← YOU ARE HERE
       │  Python function calls
       ▼
  mcp/tools/*.py         ← thin wrappers
       │  Python imports
       ▼
  agents/**/*_agent.py   ← original, UNCHANGED agents
       │
       ▼
  engines/rag_engine.py + ChromaDB + Ollama LLM

Tool catalogue
--------------
  reminders.set          Set a reminder from natural language
  reminders.list         List all reminders
  reminders.delete       Delete reminders by keyword

  email.search           Search inbox by natural language
  email.summarize        Summarise inbox or filtered emails
  email.list_all         Return raw email list

  documents.search       RAG search over local documents
  documents.summarize    Summarise all documents
  documents.topics       Extract main topics from documents
  documents.list         List available document files

  audio.transcribe       Transcribe an audio file and index in ChromaDB
  audio.query            Q&A over audio transcripts with timestamps
  audio.list             List all indexed audio files

  system.chat            Free-form LLM conversation
  system.intent          Classify a user message intent
  system.status          Server + dependency health check
"""

from __future__ import annotations

import sys
import os

# ── project root on path ───────────────────────────────────────────────────
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))   # mcp/
_ROOT       = os.path.dirname(_SERVER_DIR)                  # project root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── mcp SDK ────────────────────────────────────────────────────────────────
try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _e:
    raise ImportError(
        "The 'mcp' package is not installed.\n"
        "Install it with:  pip install mcp\n"
        f"Original error: {_e}"
    ) from _e

# ── tool implementation modules (local agent_mcp package) ─────────────────
from agent_mcp.tools.reminders import reminders_set, reminders_list, reminders_delete
from agent_mcp.tools.emails    import email_search, email_summarize, email_list_all
from agent_mcp.tools.documents import (
    documents_search, documents_summarize, documents_topics, documents_list,
)
from agent_mcp.tools.system    import system_chat, system_intent, system_status
from agent_mcp.tools.audio     import audio_transcribe, audio_query, audio_list

# ══════════════════════════════════════════════════════════════════════════════
# Server instance
# ══════════════════════════════════════════════════════════════════════════════
mcp = FastMCP(
    name="LocalAIAssistant",
    instructions=(
        "You are a personal AI assistant with access to local reminders, emails, "
        "and a document knowledge base. Use the specialised tools first; fall back "
        "to system.chat only when no other tool is more appropriate."
    ),
)

# ══════════════════════════════════════════════════════════════════════════════
# ── REMINDER TOOLS ────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool(name="reminders.set")
def tool_reminders_set(query: str) -> dict:
    """
    Create a new reminder from a natural-language request.

    Supports expressions such as:
      • "Remind me to call John at 15:30"
      • "Remind me to take medicine in 20 minutes"
      • "Set a reminder for tomorrow at 9 am to send the report"

    Args:
        query: The full natural-language reminder request.

    Returns:
        A dict with keys: success, message, reminder_text, reminder_time.
    """
    return reminders_set(query)


@mcp.tool(name="reminders.list")
def tool_reminders_list() -> dict:
    """
    List all pending and recently fired reminders.

    Returns:
        A dict with keys: success, message (formatted text), reminders (raw list).
    """
    return reminders_list()


@mcp.tool(name="reminders.delete")
def tool_reminders_delete(keyword: str) -> dict:
    """
    Delete all reminders whose text contains the given keyword.

    Args:
        keyword: Word or phrase to match (case-insensitive).
                 Example: "call John", "medicine", "send report"

    Returns:
        A dict with keys: success, message.
    """
    return reminders_delete(keyword)


# ══════════════════════════════════════════════════════════════════════════════
# ── EMAIL TOOLS ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool(name="email.search")
def tool_email_search(query: str, max_results: int = 8) -> dict:
    """
    Search the email inbox using a natural-language query.

    Performs semantic + keyword search across subject, body, and sender.
    Results are returned newest-first.

    Args:
        query:       Natural-language search query.
                     Examples: "invoice from Amazon", "meeting with Sarah"
        max_results: Maximum number of emails to return (default 8, max 50).

    Returns:
        A dict with keys: success, query, total_found, summary, emails.
    """
    return email_search(query, max_results=max_results)


@mcp.tool(name="email.summarize")
def tool_email_summarize(query: str = "") -> dict:
    """
    Summarise the email inbox.

    When query is empty, returns a summary of all available emails.
    When query is provided, returns a focused summary of matching emails.

    Args:
        query: Optional filter — topic, sender name, or keyword.
               Leave blank for a full inbox summary.

    Returns:
        A dict with keys: success, mode, query, summary.
    """
    return email_summarize(query)


@mcp.tool(name="email.list_all")
def tool_email_list_all(limit: int = 20) -> dict:
    """
    Return the most recent emails from the inbox cache.

    Args:
        limit: Maximum number of emails (default 20, max 200).

    Returns:
        A dict with keys: success, total, emails.
    """
    return email_list_all(limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# ── DOCUMENT / RAG TOOLS ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

from configs.llm_config import MODEL_NAME

@mcp.tool(name="documents.search")
def tool_documents_search(query: str, model: str = MODEL_NAME) -> dict:
    """
    Answer a question using the local document knowledge base (RAG).

    Searches ChromaDB for relevant chunks, then uses the local LLM to
    synthesise a grounded, precise answer.  If a specific filename is
    mentioned (e.g. "company_data.csv"), that file is loaded directly.

    Args:
        query: Natural-language question.
               Examples: "What is the company revenue?",
                         "Summarise the internship report",
                         "What does company_data.csv say about Q3?"
        model: Ollama model name (default: gemma:7b).

    Returns:
        A dict with keys: success, query, answer, source.
    """
    print(f"[LLM] Using model: {model}")
    return documents_search(query, model=model)


@mcp.tool(name="documents.summarize")
def tool_documents_summarize(model: str = MODEL_NAME) -> dict:
    """
    Produce a high-level summary of ALL documents in the knowledge base.

    Args:
        model: Ollama model name (default: gemma:7b).

    Returns:
        A dict with keys: success, document_count, summary.
    """
    print(f"[LLM] Using model: {model}")
    return documents_summarize(model=model)


@mcp.tool(name="documents.topics")
def tool_documents_topics(model: str = MODEL_NAME) -> dict:
    """
    Identify and group the main topics across all local documents.

    Returns:
        A dict with keys: success, document_count, topics (bullet-point list).
    """
    print(f"[LLM] Using model: {model}")
    return documents_topics(model=model)


@mcp.tool(name="documents.list")
def tool_documents_list() -> dict:
    """
    List all document files available in the local knowledge base.

    Returns:
        A dict with keys: success, count, files (list of {name,ext,size_kb}),
        summary (formatted text).
    """
    return documents_list()


# ══════════════════════════════════════════════════════════════════════════════
# ── AUDIO TOOLS ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool(name="audio.transcribe")
def tool_audio_transcribe(
    file_path: str,
    model_size: str = "base",
) -> dict:
    """
    Transcribe an audio file and index the transcript in ChromaDB.

    Accepts .wav, .mp3, .m4a, .flac, .ogg, .webm files.
    Timestamps are preserved for every transcript chunk so that
    audio.query can reference exact positions in the recording.

    Example input from user:
      "Transcribe data/audio/meeting.mp3"
      "Index the voice note at /home/user/note.wav"

    Args:
        file_path:  Path to the audio file (absolute or relative to project root).
        model_size: Faster-Whisper model size: tiny | base | small | medium | large
                    (default "base" — good accuracy, fast on CPU).

    Returns:
        A dict with keys: success, filename, duration, segments,
        chunks_stored, transcript_preview.
    """
    return audio_transcribe(file_path, model_size=model_size)


@mcp.tool(name="audio.query")
def tool_audio_query(
    query: str,
    filename: str = "",
    model: str = MODEL_NAME,
    top_k: int = 5,
) -> dict:
    """
    Answer a question about a transcribed audio recording.

    Runs a semantic search over all indexed audio transcript chunks,
    retrieves the most relevant segments (with timestamps), and asks
    the LLM to synthesise a grounded answer referencing exact timestamps.

    Example queries:
      "What was discussed in the meeting?"
      "Summarise the audio note."
      "When was robotics mentioned in the meeting?"
      "What action items were agreed?"

    Args:
        query:    Natural-language question about the audio content.
        filename: Optional — restrict search to a specific audio file.
                  Leave blank to search ALL indexed audio.
        model:    Ollama model name (default: gemma:7b).
        top_k:    Number of transcript chunks to retrieve (default 5).

    Returns:
        A dict with keys: success, query, answer,
        sources [{filename, start_ts, end_ts, snippet}].
    """
    print(f"[LLM] Using model: {model}")
    return audio_query(query, filename=filename, model=model, top_k=top_k)


@mcp.tool(name="audio.list")
def tool_audio_list() -> dict:
    """
    List all audio files that have been transcribed and indexed.

    Returns:
        A dict with keys: success, files [{filename, source, indexed_date, duration}],
        message (human-readable summary).
    """
    return audio_list()


# ══════════════════════════════════════════════════════════════════════════════
# ── SYSTEM TOOLS ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool(name="system.chat")
def tool_system_chat(
    message: str,
    model: str = MODEL_NAME,
    temperature: float = 0.7,
) -> dict:
    """
    Send a free-form message to the local LLM (Ollama).

    Use this tool for conversational messages, opinions, jokes, and any
    question that does NOT require reminders, email, or document data.

    Args:
        message:     The user's message or question.
        model:       Ollama model name (default: gemma:7b).
        temperature: 0.0 (factual) to 1.0 (creative). Default 0.7.

    Returns:
        A dict with keys: success, message, reply, model.
    """
    print(f"[LLM] Using model: {model}")
    return system_chat(message, model=model, temperature=temperature)


@mcp.tool(name="system.intent")
def tool_system_intent(message: str) -> dict:
    """
    Classify the intent of a user message.

    Returns the same intent label used by smart_agent.py routing, so an
    external client can pre-select the right tool before calling it.

    Args:
        message: The raw user utterance to classify.

    Returns:
        A dict with keys: success, message, intent, description.

    Intent labels: GREETING, TIME, DATE, REMINDER_SET, REMINDER_LIST,
    REMINDER_DELETE, EMAIL_SUMMARY, EMAIL_SEARCH, DOCUMENT_LIST,
    SUMMARY, TOPIC, RETRIEVAL, COMPARE, CHAT, GENERAL
    """
    return system_intent(message)


@mcp.tool(name="system.status")
def tool_system_status() -> dict:
    """
    Return a health-check snapshot of the MCP server and its dependencies.

    Checks Ollama availability, ChromaDB vector store, document count,
    and reminder store.

    Returns:
        A dict with keys: success, timestamp, ollama_available, model,
        vector_store_ready, document_count, reminders_count.
    """
    return system_status()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main(transport: str = "stdio", port: int = 8765, list_tools: bool = False):
    """Start the MCP server.

    Parameters
    ----------
    transport : "stdio" | "sse"
        stdio  — used by Claude Desktop, VS Code extensions, most MCP clients.
        sse    — HTTP Server-Sent Events; useful for remote / multi-client access.
    port : int
        Port for SSE transport (default 8765).
    list_tools : bool
        When True, print registered tool names and exit (smoke-test).
    """
    if list_tools:
        tools = [t for t in dir(mcp) if not t.startswith("_")]
        registered = [
            "reminders.set", "reminders.list", "reminders.delete",
            "email.search", "email.summarize", "email.list_all",
            "documents.search", "documents.summarize", "documents.topics",
            "documents.list",
            "system.chat", "system.intent", "system.status",
            "audio.transcribe", "audio.query", "audio.list",
        ]
        print("Registered MCP tools:")
        for name in registered:
            print(f"  • {name}")
        return

    if transport == "sse":
        mcp.run(transport="sse", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Local AI Assistant — MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Port for SSE transport (default: 8765)",
    )
    parser.add_argument(
        "--list-tools", action="store_true",
        help="Print registered tools and exit",
    )
    args = parser.parse_args()
    main(transport=args.transport, port=args.port, list_tools=args.list_tools)
