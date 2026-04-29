"""
engines/rag_engine.py
======================
Production RAG (Retrieval-Augmented Generation) pipeline.

Pipeline
--------
User Query
  → embed (HuggingFace sentence-transformers)
  → vector search — top *initial_k* candidates (ChromaDB)
  → rerank         — top *top_k* selected by KeywordReranker / CrossEncoder
  → inject context into LLM prompt
  → LLM answer (Ollama)
  → return (answer, source)

Public API
----------
``retrieve_top_k(query, vector_db, k)``
    Raw vector search; returns ``[(Document, score), ...]``.

``retrieve_and_rerank(query, vector_db, initial_k, top_k)``
    Vector search → rerank → top *top_k* docs.

``rag_answer(query, vector_db, model_name, initial_k, top_k, threshold)``
    Full end-to-end: retrieve → rerank → LLM → ``(answer, source)``.

Legacy helpers (kept for backward compatibility)
-------------------------------------------------
``load_embeddings``, ``create_vector_db``, ``load_vector_db``,
``retrieve_answer`` — unchanged signatures.
"""

from __future__ import annotations

from typing import Any, Optional

from core.logging_config import get_logger

log = get_logger(__name__)

# (Document, float) pairs — ChromaDB similarity_search_with_score format
_DocScore = tuple[Any, float]

# ---------------------------------------------------------------------------
# Embedding helpers (legacy — still used by services/vector_store_service.py)
# ---------------------------------------------------------------------------

def load_embeddings(model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
    """Load a HuggingFace embedding model for CPU inference."""
    from langchain_community.embeddings import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": "cpu"},
    )


def _default_vector_store_path() -> str:
    """Return the writable default persist directory for the legacy helpers."""
    try:
        from configs.settings import DATA_DIR as _DATA_DIR
        return str(_DATA_DIR / "vector_store")
    except Exception:
        return "data/vector_store"


def create_vector_db(chunks: list, persist_directory: str | None = None):
    """Create and persist a new Chroma vector store from *chunks*."""
    if persist_directory is None:
        persist_directory = _default_vector_store_path()
    from langchain_community.vectorstores import Chroma
    emb = load_embeddings()
    return Chroma.from_documents(
        documents=chunks,
        embedding=emb,
        persist_directory=persist_directory,
    )


def load_vector_db(persist_directory: str | None = None):
    """Load an existing persisted Chroma vector store."""
    if persist_directory is None:
        persist_directory = _default_vector_store_path()
    from langchain_community.vectorstores import Chroma
    emb = load_embeddings()
    return Chroma(embedding_function=emb, persist_directory=persist_directory)


# ---------------------------------------------------------------------------
# Step 1 — Vector retrieval
# ---------------------------------------------------------------------------

def retrieve_top_k(
    query: str,
    vector_db,
    k: int = 10,
) -> list[_DocScore]:
    """Run a vector similarity search and return up to *k* (doc, score) pairs."""
    if vector_db is None:
        return []
    try:
        results: list[_DocScore] = vector_db.similarity_search_with_score(query, k=k)
        return results
    except Exception as exc:
        log.warning("Vector search failed: %s", exc)
        return []


def retrieve_top_k_multi(
    query: str,
    vector_dbs: list,
    k: int = 10,
) -> list[_DocScore]:
    """Search across multiple Chroma stores and return merged, score-sorted results.

    Duplicate chunks (same content prefix) are deduplicated.  Results are
    sorted by ascending score (lower = more similar for L2 distance) and
    capped at *k* entries.

    Parameters
    ----------
    query:
        User question to embed and search.
    vector_dbs:
        List of Chroma instances to query.  ``None`` entries are skipped.
    k:
        Maximum results to return after merging and deduplication.
    """
    merged: list[_DocScore] = []
    seen: set[str] = set()

    for i, db in enumerate(vector_dbs):
        if db is None:
            continue
        store_label = getattr(db, "_persist_directory", None) or f"store[{i}]"
        try:
            batch = db.similarity_search_with_score(query, k=k)
            log.info(
                "retrieve_top_k_multi: store %r → %d result(s)",
                store_label, len(batch),
            )
            for doc, score in batch:
                # Deduplicate by first 120 characters of content
                key = doc.page_content[:120]
                if key not in seen:
                    seen.add(key)
                    merged.append((doc, score))
        except Exception as exc:
            log.warning("Vector search failed on store %r: %s", store_label, exc)

    merged.sort(key=lambda x: x[1])
    log.info(
        "retrieve_top_k_multi: %d store(s) queried → %d unique result(s) merged",
        sum(1 for db in vector_dbs if db is not None),
        len(merged),
    )
    return merged[:k]


# ---------------------------------------------------------------------------
# Step 2 — Rerank
# ---------------------------------------------------------------------------

def retrieve_and_rerank(
    query: str,
    vector_db,
    initial_k: int = 10,
    top_k: int = 3,
    prefer_cross_encoder: bool = False,
) -> list[_DocScore]:
    """Vector search → rerank → return best *top_k* (doc, score) pairs."""
    from engines.reranker import get_reranker

    candidates = retrieve_top_k(query, vector_db, k=initial_k)
    if not candidates:
        return []

    reranker = get_reranker(prefer_cross_encoder=prefer_cross_encoder)
    reranked = reranker.rerank(query, candidates, top_k=top_k)
    log.debug(
        "RAG: %d candidates → reranked to %d (query=%r)",
        len(candidates), len(reranked), query[:60],
    )
    return reranked


# ---------------------------------------------------------------------------
# Step 3 — LLM synthesis
# ---------------------------------------------------------------------------

def _build_context(docs: list[_DocScore]) -> tuple[str, str]:
    """Concatenate document chunks into context text and collect sources."""
    parts: list[str] = []
    sources: list[str] = []
    seen_sources: set[str] = set()

    for doc, _score in docs:
        content = doc.page_content.strip()
        if content:
            parts.append(content)
        src = doc.metadata.get("source", "")
        if src and src not in seen_sources:
            seen_sources.add(src)
            sources.append(src)

    context = "\n\n---\n\n".join(parts)
    source_str = ", ".join(sources) if sources else ""
    return context, source_str


def _get_rag_system_prompt() -> str:
    """Build the RAG system prompt with dynamic paths from settings."""
    try:
        from configs.settings import settings
        docs_path = str(settings.windows_docs_path)
    except Exception:
        docs_path = "the configured documents folder"
    return (
        "You are a Local Multi-Agent AI Assistant running on the user's computer. "
        f"You have access ONLY to documents indexed from the configured folder. "
        "Answer using ONLY information present in the CONTEXT provided. "
        "If the user mentions a specific filename (.pdf, .txt, .csv, .docx etc.), "
        "answer ONLY from that exact file's content if it appears in the context. "
        "Never answer from a different document unless that document was actually retrieved. "
        "Do NOT repeatedly answer from the same document unless the retrieved context actually comes from that document. "
        "Be concise — 1 to 3 sentences maximum unless detail is requested. "
        "State numbers, names, and dates directly. "
        "Never hallucinate, guess, or fabricate document content. "
        "If the user asks about files in any other folder, say: "
        "'I do not have access to that file or folder.' "
        "If the context lacks the answer, say: "
        "'The indexed documents do not contain information about this.'"
    )



def rag_answer(
    query: str,
    vector_db,
    model_name: Optional[str] = None,
    initial_k: int = 10,
    top_k: int = 5,
    threshold: float = 1.5,
    prefer_cross_encoder: bool = False,
) -> tuple[Optional[str], Optional[str]]:
    """Full RAG pipeline: retrieve → rerank → LLM → (answer, source).

    Parameters
    ----------
    query:
        User question.
    vector_db:
        Loaded Chroma vector store.
    model_name:
        Ollama model name. Defaults to ``settings.model_name``.
    initial_k:
        Number of candidates fetched from the vector store before reranking.
    top_k:
        Number of chunks passed to the LLM after reranking.
    threshold:
        Embedding distance threshold — candidates with score > threshold
        are considered too dissimilar and dropped before reranking.
    prefer_cross_encoder:
        Use the cross-encoder reranker if available.

    Returns
    -------
    (answer, source) — both strings, or (None, None) on failure.
    """
    if model_name is None:
        try:
            from configs.llm_config import MODEL
            model_name = MODEL
        except Exception:
            model_name = "qwen2.5:3b"

    # Retrieve candidates
    candidates = retrieve_top_k(query, vector_db, k=initial_k)
    if not candidates:
        return None, None

    # Apply distance threshold filter
    filtered = [(doc, score) for doc, score in candidates if score <= threshold]
    if not filtered:
        log.debug("RAG: all %d candidates exceeded threshold %.2f", len(candidates), threshold)
        # Fall back to best single match if everything is above threshold
        filtered = [candidates[0]]

    # Rerank
    from engines.reranker import get_reranker
    reranker = get_reranker(prefer_cross_encoder=prefer_cross_encoder)
    top_docs = reranker.rerank(query, filtered, top_k=top_k)

    # Build context
    context, source = _build_context(top_docs)
    if not context:
        return None, None

    # LLM synthesis
    try:
        import ollama
        print(f"[LLM] Using model: {model_name}")
        resp = ollama.chat(
            model=model_name,
            options={"temperature": 0.0, "num_predict": 250},
            messages=[
                {"role": "system", "content": _get_rag_system_prompt()},
                {
                    "role": "user",
                    "content": (
                        f"CONTEXT:\n{context}\n\n"
                        f"QUESTION: {query}\n\n"
                        f"ANSWER:"
                    ),
                },
            ],
        )
        answer = resp.get("message", {}).get("content", "").strip()
        return (answer or None), (source or None)
    except Exception as exc:
        log.error("RAG LLM call failed: %s", exc)
        return None, None


# ---------------------------------------------------------------------------
# Legacy helper (backward compat)
# ---------------------------------------------------------------------------

def retrieve_answer(
    query: str,
    vector_db,
    threshold: float = 1.5,
) -> tuple[Optional[str], Optional[str]]:
    """Legacy single-result retrieval (no reranking, no LLM).

    Kept for backward-compatibility with older call sites.
    Use ``rag_answer()`` for the full pipeline.
    """
    results = retrieve_top_k(query, vector_db, k=1)
    if not results:
        return None, None
    doc, score = results[0]
    if score > threshold:
        return None, None
    return doc.page_content, doc.metadata.get("source")


# ---------------------------------------------------------------------------
# Progressive retrieval  — FAST / DEEP modes
# ---------------------------------------------------------------------------

_DEEP_QUERY_RE = None  # compiled lazily


def _is_deep_query(query: str) -> bool:
    """Return True when the query looks like it needs full RAG (DEEP mode).

    Simple noun/filename-only queries → FAST.
    Question-form queries (who/what/why/how/explain/…) → DEEP.
    """
    global _DEEP_QUERY_RE
    if _DEEP_QUERY_RE is None:
        import re as _re
        _DEEP_QUERY_RE = _re.compile(
            r"\b(who|what|why|when|how|where|explain|describe|analyze|analyse"
            r"|tell me|list all|summarize|summarise|compare|vs|versus"
            r"|what is|what are|what does|does it|is there|are there)\b",
            _re.IGNORECASE,
        )
    return bool(_DEEP_QUERY_RE.search(query))


def fast_retrieve(
    query: str,
    file_path: str,
    keyword: Optional[str] = None,
    window: int = 200,
    max_windows: int = 3,
) -> tuple[Optional[str], Optional[str]]:
    """FAST mode: keyword-window extraction from disk, no vector search.

    Returns (text_snippet, source) or (None, None) on miss.
    """
    try:
        from services.file_indexer_service import file_indexer
        kw = keyword or query.split()[0] if query.strip() else ""
        snippet = file_indexer.keyword_window(file_path, kw, window=window, max_windows=max_windows)
        if snippet:
            return snippet, file_path
        # Fallback: partial extract
        partial = file_indexer.extract_partial(file_path, max_chars=3000)
        if partial:
            return partial, file_path
    except Exception as exc:
        log.debug("fast_retrieve failed for %r: %s", file_path, exc)
    return None, None


def progressive_retrieve(
    query: str,
    vector_db,
    file_path: Optional[str] = None,
    model_name: Optional[str] = None,
    threshold: float = 1.5,
    top_k: int = 5,
) -> tuple[Optional[str], Optional[str]]:
    """Select FAST or DEEP retrieval based on query complexity.

    FAST mode
    ---------
    - Short query (≤ 4 words) OR no question-form verbs
    - ``file_path`` is provided and on disk
    - Returns keyword-window text directly (no LLM)

    DEEP mode
    ---------
    - Question-form query  OR  FAST mode produced no result
    - Full RAG pipeline via ``rag_answer()``

    Returns (answer, source).
    """
    # Try FAST path first when a concrete file is given
    if file_path:
        import os as _os
        if _os.path.isfile(file_path) and not _is_deep_query(query):
            log.debug("[PROGRESSIVE] FAST mode for %r", file_path)
            fast_ans, fast_src = fast_retrieve(query, file_path, keyword=query.split()[0] if query.strip() else "")
            if fast_ans:
                return fast_ans, fast_src
            log.debug("[PROGRESSIVE] FAST miss — falling through to DEEP")

    # DEEP path: full RAG
    if vector_db is not None:
        log.debug("[PROGRESSIVE] DEEP mode (vector_db)")
        return rag_answer(
            query,
            vector_db,
            model_name=model_name,
            threshold=threshold,
            top_k=top_k,
        )

    # Last resort: FAST with no keyword filter
    if file_path:
        return fast_retrieve(query, file_path)

    return None, None
