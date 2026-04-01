"""
agents/knowledge/email_retrieval_agent.py
=========================================
Semantic email search using embeddings and ChromaDB.

Converts email queries to embeddings and searches for similar emails,
with optional reranking and relevance scoring.

Usage::

    from agents.knowledge.email_retrieval_agent import semantic_email_search

    results = semantic_email_search(
        query="meeting notes from john",
        top_k=5,
        threshold=0.6
    )
    # Returns: list of {"email": {...}, "score": 0.85, "reason": "..."}
"""

import logging
from typing import Optional, Any

from configs.settings import settings
from engines.embedding_engine import get_embedding_engine
from services.email_vector_store_service import get_email_vector_store_service

log = logging.getLogger(__name__)


def semantic_email_search(
    query: str,
    top_k: int = 10,
    threshold: float = 0.4,
    similarity_metric: str = "cosine"
) -> list[dict]:
    """
    Search emails semantically using embeddings.

    Parameters
    ----------
    query : str
        Natural language search query (e.g., "emails from john about the project")
    top_k : int
        Number of results to return (default: 10)
    threshold : float
        Minimum similarity score to include (0-1, default: 0.4)
    similarity_metric : str
        Similarity metric: "cosine" (default) or "euclidean"

    Returns
    -------
    list[dict]
        List of matching emails with metadata:
        [
            {
                "id": "email_id",
                "sender": "john@example.com",
                "subject": "Project Update",
                "date": "2024-01-15",
                "score": 0.85,
                "reason": "matched subject 'project'"
            },
            ...
        ]

    Raises
    ------
    RuntimeError
        If vector store or embedding engine not ready.
    """
    if not query or not isinstance(query, str):
        log.warning("Invalid query: %s", query)
        return []

    query = query.strip()
    if not query:
        return []

    # Get embedding engine and vector store
    engine = get_embedding_engine(settings.email_embedding_model)
    store = get_email_vector_store_service()

    if not engine.is_ready:
        log.warning("Embedding engine not ready")
        return []

    if not store.is_ready:
        log.warning("Email vector store not ready")
        return []

    db = store.get_vector_db()
    if not db:
        log.error("Vector store is None despite is_ready=True")
        return []

    try:
        # Embed query
        query_embedding = engine.embed(query, normalize=True)
        if not query_embedding:
            log.error("Failed to embed query")
            return []

        log.debug("Query embedding dim: %d", len(query_embedding))

        # Query ChromaDB
        results = db.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=None,  # All emails
        )

        if not results or not results.get("ids"):
            log.info("No emails found for query: %s", query)
            return []

        # Format results
        emails = []
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        documents = results.get("documents", [[]])[0]

        for i, email_id in enumerate(ids):
            # Convert distance to similarity score (cosine: distance ~ 1 - similarity)
            similarity = 1 - distances[i]

            # Filter by threshold
            if similarity < threshold:
                log.debug("Skipping email %s (score %.3f < threshold)", email_id, similarity)
                continue

            metadata = metadatas[i] if i < len(metadatas) else {}
            doc = documents[i] if i < len(documents) else ""

            email_result = {
                "id": email_id,
                "sender": metadata.get("sender", ""),
                "subject": metadata.get("subject", ""),
                "date": metadata.get("date", ""),
                "score": round(similarity, 3),
                "reason": _explain_match(query, metadata),
            }
            emails.append(email_result)

        log.info("Found %d emails matching '%s'", len(emails), query)
        return emails

    except Exception as e:
        log.error("Semantic search failed: %s", e, exc_info=True)
        return []


def _explain_match(query: str, metadata: dict) -> str:
    """Generate human-readable explanation for match."""
    subject = metadata.get("subject", "").lower()
    query_lower = query.lower()

    # Simple keyword matching for explanation
    if query_lower in subject:
        return "matched in subject"
    elif query_lower.split()[0] in subject if query_lower else False:
        return f"subject contains '{query_lower.split()[0]}'"
    elif any(word in subject for word in query_lower.split() if len(word) > 3):
        words = [w for w in query_lower.split() if len(w) > 3 and w in subject]
        if words:
            return f"subject contains: {', '.join(words[:2])}"

    return "semantic match"


def filter_by_sender(emails: list[dict], sender_pattern: str) -> list[dict]:
    """
    Filter semantic search results by sender email/name.

    Parameters
    ----------
    emails : list[dict]
        Results from semantic_email_search()
    sender_pattern : str
        Substring to match in sender field (case-insensitive)

    Returns
    -------
    list[dict]
        Filtered emails
    """
    if not sender_pattern:
        return emails

    pattern = sender_pattern.lower()
    return [e for e in emails if pattern in e.get("sender", "").lower()]


def filter_by_date_range(
    emails: list[dict],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> list[dict]:
    """
    Filter results by date range (ISO format: YYYY-MM-DD).

    Parameters
    ----------
    emails : list[dict]
        Results from semantic_email_search()
    start_date : str, optional
        Earliest date (inclusive)
    end_date : str, optional
        Latest date (inclusive)

    Returns
    -------
    list[dict]
        Filtered emails
    """
    if not start_date and not end_date:
        return emails

    filtered = []
    for email in emails:
        date_str = email.get("date", "")
        if not date_str:
            continue

        # Simple string comparison (works for ISO format)
        if start_date and date_str < start_date:
            continue
        if end_date and date_str > end_date:
            continue

        filtered.append(email)

    return filtered


def get_email_full_content(email_id: str, email_cache: dict) -> Optional[dict]:
    """
    Retrieve full email content from cache using ID.

    Parameters
    ----------
    email_id : str
        Email ID from search results
    email_cache : dict
        Full email data (from email_agent or email_query_agent)

    Returns
    -------
    dict or None
        Full email with body, attachments, etc.
    """
    # Search in emails list
    emails = email_cache.get("emails", [])
    for email in emails:
        if str(email.get("id")) == email_id:
            return email

    log.warning("Email %s not found in cache", email_id)
    return None
