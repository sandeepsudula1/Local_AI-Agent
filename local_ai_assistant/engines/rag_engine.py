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


def create_vector_db(chunks: list, persist_directory: str = "data/vector_store"):
    """Create and persist a new Chroma vector store from *chunks*."""
    from langchain_community.vectorstores import Chroma
    emb = load_embeddings()
    return Chroma.from_documents(
        documents=chunks,
        embedding=emb,
        persist_directory=persist_directory,
    )


def load_vector_db(persist_directory: str = "data/vector_store"):
    """Load an existing persisted Chroma vector store."""
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


_RAG_SYSTEM_PROMPT = (
    "You are a precise document analysis assistant. "
    "Answer using ONLY facts explicitly stated in the CONTEXT provided. "
    "Be concise — 1 to 3 sentences maximum unless detail is requested. "
    "State numbers, names, and dates directly. "
    "Do NOT add information not present in the CONTEXT. "
    "If the context lacks the answer, say: "
    "'The document does not contain that information.'"
)


def rag_answer(
    query: str,
    vector_db,
    model_name: Optional[str] = None,
    initial_k: int = 10,
    top_k: int = 3,
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
            from configs.settings import settings
            model_name = settings.model_name
        except Exception:
            model_name = "llama3.2:1b"

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
        resp = ollama.chat(
            model=model_name,
            options={"temperature": 0.0, "num_predict": 250},
            messages=[
                {"role": "system", "content": _RAG_SYSTEM_PROMPT},
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
