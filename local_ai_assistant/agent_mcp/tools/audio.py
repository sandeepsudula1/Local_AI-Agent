"""
agent_mcp/tools/audio.py
========================
MCP tool wrappers for the Audio Intelligence subsystem.

Wraps (without modifying):
  • agents/knowledge/audio_agent.py
        transcribe_and_index, query_audio, list_audio_files

Exposed MCP tools
-----------------
  audio.transcribe   — upload + transcribe + index an audio file
  audio.query        — semantic Q&A over indexed audio with timestamps
  audio.list         — list all indexed audio files
"""

from __future__ import annotations

import sys
import os

# ── project root on path ──────────────────────────────────────────────────
_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT    = os.path.dirname(_MCP_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── underlying agent ──────────────────────────────────────────────────────
from agents.knowledge.audio_agent import (
    transcribe_and_index,
    query_audio,
    list_audio_files,
)


# ── MCP tool functions ────────────────────────────────────────────────────

def audio_transcribe(
    file_path: str,
    model_size: str = "base",
) -> dict:
    """
    Transcribe an audio file and index the transcript in ChromaDB.

    Accepts .wav, .mp3, .m4a, .flac, .ogg, .webm files.
    After indexing the audio can be queried with audio_query().

    Parameters
    ----------
    file_path : str
        Absolute path or path relative to the project root.
        Example: "data/audio/meeting_2026_03_04.mp3"
    model_size : str
        Whisper model size: tiny | base | small | medium | large
        (default "base" — good accuracy on CPU, ~75 MB)

    Returns
    -------
    dict
        success, filename, duration, segments, chunks_stored,
        transcript_preview
    """
    return transcribe_and_index(file_path, model_size=model_size)


from configs.llm_config import MODEL_NAME

def audio_query(
    query: str,
    filename: str = "",
    model: str = MODEL_NAME,
    top_k: int = 5,
) -> dict:
    """
    Answer a question about a transcribed audio file.

    Searches indexed transcript chunks semantically, retrieves the most
    relevant segments (with timestamps), and asks the LLM to synthesise
    a timestamped answer.

    Parameters
    ----------
    query : str
        Natural-language question.
        Examples:
          • "What was discussed in the meeting?"
          • "Summarise the audio note."
          • "When was robotics mentioned?"
    filename : str
        Optional — restrict search to a specific audio file.
        Leave blank to search ALL indexed audio files.
    model : str
        Ollama model to use (default gemma:7b).
    top_k : int
        Number of transcript chunks to retrieve (default 5).

    Returns
    -------
    dict
        success, query, answer, sources
        sources: [{filename, start_ts, end_ts, snippet}, ...]
    """
    print(f"[LLM] Using model: {model}")
    return query_audio(
        query,
        filename=filename or None,
        model=model,
        top_k=top_k,
    )


def audio_list() -> dict:
    """
    List all audio files that have been indexed.

    Returns
    -------
    dict
        success, files [{filename, source, indexed_date, duration}],
        message (human-readable summary)
    """
    return list_audio_files()
