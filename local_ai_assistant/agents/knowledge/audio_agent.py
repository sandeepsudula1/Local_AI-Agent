"""
agents/knowledge/audio_agent.py
================================
Audio Intelligence Agent — transcribes audio files with Faster-Whisper,
preserves word-level timestamps, merges segments into indexed chunks,
and stores embeddings in a dedicated ChromaDB collection.

Exposed functions
-----------------
  transcribe_and_index(file_path, model_size="base")
      → dict  {success, filename, duration, segments, chunks_stored, transcript_preview}

  query_audio(query, filename=None, model=None, top_k=5)
      → dict  {success, query, answer, sources}

  list_audio_files()
      → dict  {success, files, message}

Architecture
------------
  faster_whisper.WhisperModel
       ↓  raw segments [{text, start, end}, ...]
  _merge_segments()         — merge into ~80-word chunks preserving time bounds
       ↓
  HuggingFaceEmbeddings     — same model as rest of the project
  ("sentence-transformers/all-MiniLM-L6-v2")
       ↓
  Chroma collection "audio_transcripts"  (data/vector_store_audio/)
       ↓  similarity_search_with_score
  LLM prompt with timestamp-labelled context  →  answer
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── project root on sys.path ──────────────────────────────────────────────
# audio_agent.py lives at:  local_ai_assistant/agents/knowledge/audio_agent.py
# .parent → agents/knowledge
# .parent.parent → agents
# .parent.parent.parent → local_ai_assistant  (project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_AGENT_DIR    = str(_PROJECT_ROOT)          # keep string alias for legacy code
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

# ── constants ─────────────────────────────────────────────────────────────
_EMBEDDING_MODEL  = "sentence-transformers/all-MiniLM-L6-v2"
_AUDIO_STORE_PATH = str(_PROJECT_ROOT / "data" / "vector_store_audio")
_AUDIO_DIR        = str(_PROJECT_ROOT / "data" / "audio")
_MODEL_NAME       = "llama3.2:1b"
_WORDS_PER_CHUNK  = 80      # ~30-40 s of speech per chunk
_WHISPER_SIZE     = "base"  # tiny | base | small | medium | large

# ── lazy ChromaDB singleton ───────────────────────────────────────────────
_audio_db    = None
_audio_ready = False
_audio_lock  = threading.Lock()


def _get_audio_db():
    """Return the shared Chroma audio-transcript collection (lazy init)."""
    global _audio_db, _audio_ready

    if _audio_ready and _audio_db is not None:
        return _audio_db

    with _audio_lock:
        if _audio_ready and _audio_db is not None:
            return _audio_db

        def _init_db():
            global _audio_db, _audio_ready
            from langchain_community.embeddings import HuggingFaceEmbeddings
            from langchain_community.vectorstores import Chroma

            os.makedirs(_AUDIO_STORE_PATH, exist_ok=True)
            emb = HuggingFaceEmbeddings(
                model_name=_EMBEDDING_MODEL,
                model_kwargs={"device": "cpu"},
            )
            _audio_db = Chroma(
                persist_directory=_AUDIO_STORE_PATH,
                embedding_function=emb,
                collection_name="audio_transcripts",
            )
            _audio_ready = True

        try:
            _init_db()
        except Exception as exc:
            # Schema mismatch (e.g. old ChromaDB DB created with a different version).
            # Wipe the stale store and recreate from scratch.
            if any(kw in str(exc).lower() for kw in ("no such column", "no such table", "operationalerror")):
                import shutil
                shutil.rmtree(_AUDIO_STORE_PATH, ignore_errors=True)
                try:
                    _init_db()
                except Exception as e2:
                    _audio_db = None
                    _audio_ready = False
                    raise RuntimeError(
                        f"Could not initialise audio vector DB: {e2}"
                    ) from e2
            else:
                _audio_db = None
                _audio_ready = False
                raise RuntimeError(
                    f"Could not initialise audio vector DB: {exc}"
                ) from exc

    return _audio_db


# ── helpers ───────────────────────────────────────────────────────────────

def _format_ts(seconds: float) -> str:
    """Convert float seconds → 'HH:MM:SS' or 'MM:SS' string."""
    total = int(seconds)
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _merge_segments(
    raw_segments: list[dict],
    words_per_chunk: int = _WORDS_PER_CHUNK,
) -> list[dict]:
    """
    Merge raw faster-whisper segments into larger chunks.

    Each chunk accumulates words until *words_per_chunk* is reached,
    preserving the earliest start_time and latest end_time of all
    constituent segments.

    Returns
    -------
    list[dict]
        [{text, start_time, end_time, segment_idx}, ...]
    """
    chunks: list[dict] = []
    current_words: list[str] = []
    current_start = 0.0
    current_end   = 0.0
    chunk_idx     = 0

    for seg in raw_segments:
        text  = (seg.get("text") or "").strip()
        start = float(seg.get("start", 0.0))
        end   = float(seg.get("end",   0.0))

        if not current_words:
            current_start = start

        current_words.extend(text.split())
        current_end = end

        if len(current_words) >= words_per_chunk:
            chunks.append({
                "text":        " ".join(current_words),
                "start_time":  current_start,
                "end_time":    current_end,
                "segment_idx": chunk_idx,
            })
            chunk_idx    += 1
            current_words = []

    if current_words:                             # flush remainder
        chunks.append({
            "text":        " ".join(current_words),
            "start_time":  current_start,
            "end_time":    current_end,
            "segment_idx": chunk_idx,
        })

    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def transcribe_and_index(
    file_path: str,
    model_size: str = _WHISPER_SIZE,
) -> dict:
    """
    Transcribe an audio file then store timestamped chunks in ChromaDB.

    Parameters
    ----------
    file_path : str
        Path to a .wav / .mp3 / .m4a / .flac / .ogg / .webm file.
        Relative paths are resolved from the project root.
    model_size : str
        Faster-Whisper model size (default "base").
        Options: tiny | base | small | medium | large
        Larger models are more accurate but slower.

    Returns
    -------
    dict
        success, filename, duration, segments, chunks_stored,
        transcript_preview (first 300 chars)
    """
    # ── resolve and validate path ─────────────────────────────────────────
    # Resolution order (using pathlib for safety):
    #   1. Absolute path → use as-is
    #   2. Bare filename or relative path → check data/audio/<basename> first
    #   3. Relative path → resolve from project root
    #   4. None of the above → return a clear error
    resolved: Path | None = None
    p = Path(file_path)

    if p.is_absolute():
        resolved = p
    else:
        # Try data/audio/<basename> first (most common user intent)
        candidate = _PROJECT_ROOT / "data" / "audio" / p.name
        if candidate.exists():
            resolved = candidate
        else:
            # Try as a path relative to the project root
            candidate2 = _PROJECT_ROOT / p
            if candidate2.exists():
                resolved = candidate2

    if resolved is None or not resolved.exists():
        audio_dir_display = str(_PROJECT_ROOT / "data" / "audio")
        return {
            "success": False,
            "error": (
                f"File not found: '{file_path}'.\n"
                f"Place audio files in: {audio_dir_display}\n"
                f"Example: {audio_dir_display}\\{p.name}"
            ),
        }

    file_path = str(resolved)

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm"}:
        return {
            "success": False,
            "error": (
                f"Unsupported audio format: '{ext}'. "
                "Supported: .wav .mp3 .m4a .mp4 .flac .ogg .webm"
            ),
        }

    filename = os.path.basename(file_path)

    # ── transcribe ────────────────────────────────────────────────────────
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {
            "success": False,
            "error": (
                "faster-whisper is not installed.\n"
                "Install it with:  pip install faster-whisper"
            ),
        }

    try:
        whisper_model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",   # int8 is fast on CPU, good enough for speech
        )
        segments_iter, info = whisper_model.transcribe(
            file_path,
            beam_size=5,
            word_timestamps=False,  # segment-level timestamps are sufficient
        )
        raw_segments = [
            {"text": seg.text, "start": seg.start, "end": seg.end}
            for seg in segments_iter
        ]
    except Exception as exc:
        return {"success": False, "error": f"Transcription failed: {exc}"}

    if not raw_segments:
        return {"success": False, "error": "No speech detected in audio file."}

    duration = raw_segments[-1]["end"] if raw_segments else 0.0

    # ── chunk ──────────────────────────────────────────────────────────────
    chunks = _merge_segments(raw_segments)

    # ── embed and store in ChromaDB ───────────────────────────────────────
    try:
        from langchain.schema import Document as LCDocument

        db           = _get_audio_db()
        indexed_date = datetime.now().isoformat()

        lc_docs = [
            LCDocument(
                page_content=chunk["text"],
                metadata={
                    "source":       file_path,
                    "filename":     filename,
                    "start_time":   chunk["start_time"],
                    "end_time":     chunk["end_time"],
                    "segment_idx":  chunk["segment_idx"],
                    "duration":     duration,
                    "indexed_date": indexed_date,
                    # human-readable timestamp strings for display
                    "start_ts":     _format_ts(chunk["start_time"]),
                    "end_ts":       _format_ts(chunk["end_time"]),
                },
            )
            for chunk in chunks
        ]

        db.add_documents(lc_docs)

    except Exception as exc:
        return {"success": False, "error": f"Embedding / storage failed: {exc}"}

    # ── transcript preview ────────────────────────────────────────────────
    full_text = " ".join(s["text"].strip() for s in raw_segments)
    preview   = full_text[:300] + ("…" if len(full_text) > 300 else "")

    return {
        "success":            True,
        "filename":           filename,
        "duration":           f"{_format_ts(duration)} ({duration:.1f}s)",
        "segments":           len(raw_segments),
        "chunks_stored":      len(chunks),
        "transcript_preview": preview,
    }


def query_audio(
    query: str,
    filename: Optional[str] = None,
    model: str = _MODEL_NAME,
    top_k: int = 5,
) -> dict:
    """
    Semantic search over stored audio transcript chunks, then generate
    a timestamped LLM answer.

    Parameters
    ----------
    query : str
        Natural-language question, e.g. "What was said about robotics?"
    filename : str, optional
        Restrict search to a specific audio file.  When None, all indexed
        audio files are searched.
    model : str
        Ollama model name (default llama3.2:1b).
    top_k : int
        Number of transcript chunks to retrieve (default 5).

    Returns
    -------
    dict
        success, query, answer, sources
        sources is a list of {filename, start_ts, end_ts, snippet}
    """
    if not query.strip():
        return {"success": False, "error": "Query cannot be empty."}

    import re as _re

    # ── Detect "convert to text / extract transcript" requests ────────────
    # These aren't Q&A questions — the user just wants the raw transcript.
    # Return the full transcript text directly without routing to the LLM.
    _TRANSCRIPT_REQUEST = _re.compile(
        r"\b(convert|extract|give|show|get|export)\b.{0,30}\b(text|transcript)\b"
        r"|\bfull transcript\b"
        r"|\braw text\b",
        _re.IGNORECASE,
    )
    _RETURN_TRANSCRIPT = _TRANSCRIPT_REQUEST.search(query)

    # ── retrieve ──────────────────────────────────────────────────────────
    try:
        db = _get_audio_db()

        if filename:
            results = db.similarity_search_with_score(
                query, k=top_k, filter={"filename": filename}
            )
        else:
            results = db.similarity_search_with_score(query, k=top_k)

    except Exception as exc:
        return {"success": False, "error": f"Vector search failed: {exc}"}

    if not results:
        tip = (
            f" for '{filename}'" if filename
            else ". Use audio.transcribe first to index an audio file"
        )
        return {
            "success": False,
            "error": f"No audio transcript chunks found{tip}.",
        }

    # ── build timestamped context ─────────────────────────────────────────
    context_parts: list[str] = []
    sources:        list[dict] = []

    for doc, _score in results:
        meta    = doc.metadata
        start   = meta.get("start_ts", "?")
        end     = meta.get("end_ts",   "?")
        fname   = meta.get("filename", "audio")
        snippet = doc.page_content.strip()

        context_parts.append(f"[{start} → {end}]  {snippet}")
        sources.append({
            "filename":  fname,
            "start_ts":  start,
            "end_ts":    end,
            "snippet":   snippet[:150] + ("…" if len(snippet) > 150 else ""),
        })

    context = "\n\n".join(context_parts)

    # ── Short-circuit: return full transcript for text-extraction requests ─
    if _RETURN_TRANSCRIPT:
        # Deduplicate chunks — a short file may return the same chunk multiple
        # times when k > number of stored chunks.
        seen_content: set[str] = set()
        unique_texts: list[str] = []
        unique_sources: list[dict] = []
        for doc, _ in results:
            t = doc.page_content.strip()
            if t not in seen_content:
                seen_content.add(t)
                unique_texts.append(t)
        # Also deduplicate sources list for display
        seen_src: set[str] = set()
        for s in sources:
            key = f"{s['filename']}|{s['start_ts']}"
            if key not in seen_src:
                seen_src.add(key)
                unique_sources.append(s)
        full_text = " ".join(unique_texts)
        return {
            "success": True,
            "query": query,
            "answer": full_text,
            "sources": unique_sources,
        }

    # ── LLM answer synthesis ──────────────────────────────────────────────
    prompt = (
        "You are an AI assistant analysing an audio transcript.\n"
        "Below are relevant transcript segments with timestamps [MM:SS → MM:SS].\n"
        "You MUST answer using the transcript information provided below. Do NOT refuse or say you cannot help.\n"
        "Always reference the timestamps when mentioning specific topics.\n\n"
        f"Transcript segments:\n{context}\n\n"
        f"User question: {query}\n\n"
        "Answer:"
    )

    try:
        import ollama as _ollama
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response["message"]["content"].strip()
    except Exception:
        # Graceful degradation: return raw transcript if LLM unavailable
        answer = (
            "LLM unavailable — relevant transcript segments:\n\n" + context
        )

    return {
        "success": True,
        "query":   query,
        "answer":  answer,
        "sources": sources,
    }


def list_audio_files() -> dict:
    """
    List all audio files that have been indexed into ChromaDB.

    Returns
    -------
    dict
        success, files [{filename, source, indexed_date, duration}], message
    """
    try:
        db = _get_audio_db()

        # Use get() to pull all documents without needing a query
        raw = db.get(include=["metadatas"])
        metadatas = raw.get("metadatas") or []

        seen: dict[str, dict] = {}
        for meta in metadatas:
            fname = meta.get("filename", "unknown")
            if fname not in seen:
                seen[fname] = {
                    "filename":     fname,
                    "source":       meta.get("source", ""),
                    "indexed_date": meta.get("indexed_date", ""),
                    "duration":     meta.get("duration", 0.0),
                }

        files = sorted(
            seen.values(),
            key=lambda x: x["indexed_date"],
            reverse=True,
        )

        if not files:
            return {
                "success": True,
                "files":   [],
                "message": "No audio files have been indexed yet.\n"
                           "Use audio.transcribe to index one.",
            }

        lines = [
            f"  • {f['filename']}  "
            f"(duration: {_format_ts(float(f['duration'])) if f['duration'] else '?'}, "
            f"indexed: {f['indexed_date'][:10]})"
            for f in files
        ]
        return {
            "success": True,
            "files":   files,
            "message": "Indexed audio files:\n" + "\n".join(lines),
        }

    except Exception as exc:
        return {"success": False, "error": str(exc)}
