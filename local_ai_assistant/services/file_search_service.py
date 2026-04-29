"""
services/file_search_service.py
================================
High-level file discovery service — metadata-based, NO RAG, NO embeddings.

Responsibilities
----------------
* Search the SQLite file index by name or keyword (delegates to
  ``FileIndexerService`` for scoring, uses ``db_service`` for raw SQL).
* Format multi-result lists for the conversational UI.
* Resolve a user's selection ("1", "second one", "resume.pdf") back to a
  full file path.
* Extract meaningful keywords from discovery-style queries ("find my resume"
  → "resume") so noise tokens don't skew scoring.

This module is the ONLY entry-point for file-discovery logic.  Content
retrieval (RAG, direct read) is handled separately in ``retrieval_agent``.

Usage::

    from services.file_search_service import file_search_service

    results = file_search_service.search("resume")
    # → [{"name": "sandeep_resume.pdf", "path": "...", ...}, ...]

    msg = file_search_service.format_listing(results)
    # → "I found 3 files:\\n1. sandeep_resume.pdf\\n..."

    path = file_search_service.resolve_selection("2", results)
    # → "/abs/path/to/Resume_2024.docx"
"""

from __future__ import annotations

import os
import re
from typing import Optional

from core.logging_config import get_logger

log = get_logger(__name__)

# Maximum files returned in a single search
_DEFAULT_LIMIT = 15

# Verbs and stopwords stripped before keyword search so "find my resume" →
# keywords = "resume" rather than accidentally matching files named "find…".
_STRIP_WORDS_RE = re.compile(
    r"\b(?:find|locate|search|look|for|my|me|a|an|the|some|any"
    r"|open|show|list|get|fetch|please|can|you|do|have|help"
    r"|read|access|load|display|pull|up|give|bring|retrieve"
    r"|where|is|are|was|what|which|there|file|files|document"
    r"|documents|doc|docs|folder|directory"
    r"|related|about|regarding|concerning|on|to|of|at|in|by|with|from|into"
    r"|topics?|concepts?|ideas?|content|things?|stuff)\b",
    re.IGNORECASE,
)


def _extract_keywords(query: str) -> str:
    """Return query stripped of discovery verbs and filler words. Filters words < 3 chars."""
    cleaned = _STRIP_WORDS_RE.sub(" ", query)
    tokens = [t.lower() for t in re.split(r"\s+", cleaned.strip()) if len(t) >= 3]
    return " ".join(tokens) if tokens else query


def _extract_entity_and_keyword(query: str) -> tuple[Optional[str], str]:
    """Extract entity names and keywords separately from a discovery query.
    
    Removes stopwords and takes the first meaningful word as the entity.
    Returns (entity_string, keyword_string).
    """
    # Remove stopwords and generic terms
    stop_pattern = re.compile(r"\b(find|show|me|email|mail|inbox|from|file|files|document|documents|related|contains|containing|about|do|to|the|which|what|can|you|is|are|in|the|above)\b", re.IGNORECASE)
    cleaned = stop_pattern.sub(" ", query)
    # Filter out words < 3 chars from the candidates
    tokens = [t for t in re.split(r"\s+", cleaned.strip()) if len(t) >= 3]
    if not tokens:
        return None, query
    
    # Priority words handling
    priority_topics = {"resume", "pizza", "report", "presentation"}
    
    # Check if any priority topic exists
    entity = None
    for t in tokens:
        if t.lower() in priority_topics:
            entity = t
            break
            
    # Fallback to the first meaningful word
    if not entity:
        entity = tokens[0]
        
    keyword = " ".join(tokens)
    return entity, keyword


# ---------------------------------------------------------------------------
# Query expansion — synonym / abbreviation mapping
# ---------------------------------------------------------------------------
# Maps common abbreviations and short-forms to their expanded equivalents.
# Both keys and values are lowercased.  Expansion is additive — the original
# query term is kept alongside the expansion so that exact-match still works.
_SYNONYM_MAP: dict[str, str] = {
    "l&t": "larsen toubro",
    "lnt": "larsen toubro",
    "l and t": "larsen toubro",
    "tcs": "tata consultancy services",
    "hcl": "hcl technologies",
    "infy": "infosys",
    "ril": "reliance industries",
    "sbi": "state bank india",
    "ms": "microsoft",
    "goog": "google alphabet",
    "amzn": "amazon",
    "fb": "facebook meta",
    "cv": "curriculum vitae resume",
    "pnl": "profit loss",
    "p&l": "profit loss",
    "bs": "balance sheet",
    "fy": "financial year",
    "qtr": "quarter quarterly",
    "q1": "quarter 1 first quarter",
    "q2": "quarter 2 second quarter",
    "q3": "quarter 3 third quarter",
    "q4": "quarter 4 fourth quarter",
    "yoy": "year over year",
    "qoq": "quarter over quarter",
    "mom": "month over month",
    "rev": "revenue",
    "govt": "government",
    "mgmt": "management",
    "dept": "department",
    "hr": "human resources",
    "it": "information technology",
    "ai": "artificial intelligence",
    "ml": "machine learning",
}


def _expand_query(query: str) -> str:
    """Expand abbreviations and synonyms in *query* using ``_SYNONYM_MAP``.

    The expansion is additive: the original token is kept alongside the
    expansion so that exact-match still works.  Only whole-word matches
    are expanded.

    Example: "L&T financial results" → "L&T larsen toubro financial results"
    """
    q_lower = query.lower()
    additions: list[str] = []
    for abbr, expansion in _SYNONYM_MAP.items():
        # Match as whole word (handle & in abbreviations)
        pattern = r"\b" + re.escape(abbr) + r"\b"
        if re.search(pattern, q_lower):
            additions.append(expansion)
    if additions:
        expanded = query + " " + " ".join(additions)
        log.debug("[QueryExpand] %r → %r", query, expanded)
        return expanded
    return query


# ---------------------------------------------------------------------------
# Ordinal word → position mapping for selection resolution
# ---------------------------------------------------------------------------
_ORDINALS: dict[str, int] = {
    "first": 1, "1st": 1, "one": 1,
    "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
    "sixth": 6, "6th": 6, "six": 6,
    "seventh": 7, "7th": 7, "seven": 7,
    "eighth": 8, "8th": 8, "eight": 8,
    "ninth": 9, "9th": 9, "nine": 9,
    "tenth": 10, "10th": 10, "ten": 10,
}


class FileSearchService:
    """Metadata-based file discovery — no content indexing or embeddings."""

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = _DEFAULT_LIMIT) -> list[dict]:
        """Hybrid search: entity-first filtering -> keyword scoring -> semantic.

        Parameters
        ----------
        query : str
            Natural-language query, e.g. "find susmitha resume"
        limit : int
            Maximum number of results.
        """
        from agents.core.general_agent import normalize_query
        query = normalize_query(query)
        
        # ── Query Type Detection ─────────────────────────────────────────
        is_content_query = bool(re.search(r"\b(containing|contains|content|about)\b", query, re.IGNORECASE))
        entity, keyword_only = _extract_entity_and_keyword(query)
        
        if is_content_query:
            print("[SEARCH] Mode: SEMANTIC")
            print("[SEARCH] Entity: None")
            print(f"[QUERY] Normalized: {query}")
            entity = None  # Force semantic search
        else:
            print("[SEARCH] Mode: ENTITY")
            print(f"[SEARCH] Entity: {entity}")
            print(f"[QUERY] Normalized: {query}")
        
        # ── Entity-First Filtering ───────────────────────────────────────
        if entity:
            try:
                from services.db_service import get_connection
                with get_connection() as conn:
                    rows = conn.execute(
                        "SELECT path, name, extension, size_bytes, mtime "
                        "FROM files WHERE LOWER(name) LIKE ? OR LOWER(path) LIKE ?",
                        (f"%{entity.lower()}%", f"%{entity.lower()}%")
                    ).fetchall()
                entity_results = [dict(r) for r in rows]
            except Exception as exc:
                log.debug("[FileSearch] entity search failed: %s", exc)
                entity_results = []
                
            print(f"[SEARCH] Entity matches: {len(entity_results)}")
            if len(entity_results) == 0:
                print("[SEARCH] Entity match=0. Falling back to semantic search.")
            else:
                # Score entity results: Exact match > Partial > Keyword
                for r in entity_results:
                    r["_entity_score"] = 0
                    name_lower = (r.get("name") or "").lower()
                    stem = os.path.splitext(name_lower)[0]
                    if entity.lower() == stem or entity.lower() == name_lower:
                        r["_entity_score"] += 100
                    elif entity.lower() in name_lower:
                        r["_entity_score"] += 50
                        
                    if keyword_only:
                        for kw in keyword_only.split():
                            if kw.lower() in name_lower or kw.lower() in (r.get("path") or "").lower():
                                r["_entity_score"] += 10
                                
                entity_results.sort(key=lambda x: -x["_entity_score"])
                
                # Clean up scores
                for r in entity_results:
                    r.pop("_entity_score", None)
                    
                return entity_results[:limit]

        # ── Fallback to Generic Keyword/Semantic Search ──────────────────
        keywords = _extract_keywords(query)
        if not keywords:
            keywords = query

        expanded_query = _expand_query(query)
        expanded_keywords = _expand_query(keywords)

        _query_requests_images = bool(re.search(
            r"\b(?:image|photo|picture|screenshot|png|jpg|jpeg|webp|gif)\b",
            query, re.IGNORECASE,
        ))
        _IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"})

        # ── Gather keyword results ───────────────────────────────────────
        kw_results: list[dict] = []
        try:
            from services.file_indexer_service import file_indexer
            kw_results = file_indexer.search(expanded_keywords, limit=limit * 2)
        except Exception as exc:
            log.debug("[FileSearch] keyword search failed: %s", exc)

        # ── Gather semantic results ──────────────────────────────────────
        sem_results: list[dict] = []
        try:
            from services.file_indexer_service import file_indexer
            sem_results = file_indexer.semantic_search(expanded_query, limit=limit * 2)
        except Exception as exc:
            log.debug("[FileSearch] semantic search failed: %s", exc)

        # ── Fuse scores ─────────────────────────────────────────────────
        # Build a path → combined-score map
        fused: dict[str, tuple[float, dict]] = {}

        # Normalise keyword scores to 0-1 range
        kw_max = max((r.get("chunk_stale", 0) for r in kw_results), default=0)
        # keyword score isn't stored in the dict; re-score inline
        kw_tokens = [t.lower() for t in re.split(r"\s+", expanded_keywords.strip()) if len(t) >= 3]
        for r in kw_results:
            name_lc = (r.get("name") or "").lower()
            hint_lc = (r.get("content_hint") or "").lower()
            raw = 0
            for tok in kw_tokens:
                if tok in name_lc:
                    raw += 4
                if tok in hint_lc:
                    raw += 1
            kw_max = max(kw_max, raw)

        for r in kw_results:
            name_lc = (r.get("name") or "").lower()
            hint_lc = (r.get("content_hint") or "").lower()
            raw = 0
            for tok in kw_tokens:
                if tok in name_lc:
                    raw += 4
                if tok in hint_lc:
                    raw += 1
            norm_kw = (raw / kw_max) if kw_max else 0.0
            p = r["path"]
            fused[p] = (0.4 * norm_kw, r)

        for r in sem_results:
            sem_score = r.pop("semantic_score", 0.0)
            p = r["path"]
            existing_score, existing_row = fused.get(p, (0.0, r))
            fused[p] = (existing_score + 0.6 * sem_score, existing_row)

        if not fused:
            # Final fallback: simple LIKE query
            try:
                from services.db_service import search_files_by_name
                first_kw = (keywords.split() or [keywords])[0]
                results = search_files_by_name(first_kw, limit=limit)
                log.info("[FileSearch] LIKE fallback → %d results", len(results))
                return results
            except Exception as exc:
                log.warning("[FileSearch] LIKE fallback failed: %s", exc)
                return []

        # Sort by fused score descending
        ranked = sorted(fused.values(), key=lambda x: -x[0])
        results = [row for _, row in ranked[:limit]]

        log.info(
            "[FileSearch] hybrid query=%r keywords=%r → %d kw + %d sem → %d fused",
            query, keywords, len(kw_results), len(sem_results), len(results),
        )

        # ── Exact filename boost and Fuzzy matching ────────────────────────
        _query_compact = re.sub(r"[\s_\-]+", "", keywords).lower()
        import difflib
        
        # Determine if it's a resume search even with typos
        is_resume_search = False
        for q_word in query.lower().split():
            if difflib.SequenceMatcher(None, q_word, "resume").ratio() >= 0.75:
                is_resume_search = True
                break
                
        _resume_preferred_exts = {".pdf", ".docx", ".txt"}
        _resume_avoid_exts = {".png", ".jpg", ".jpeg", ".webp"}
        
        new_scored_results = []
        for s, r in ranked:
            name_lc = (r.get("name") or "").lower()
            name_compact = re.sub(r"[\s_\-]+", "", os.path.splitext(name_lc)[0])
            ext_lc = (r.get("extension") or "").lower()
            
            # Exact boost
            if _query_compact and _query_compact in name_compact:
                s += 0.8
                r["_exact_boost"] = True
            else:
                r["_exact_boost"] = False
                
            # Fuzzy match keywords
            for kw in keywords.split():
                if len(kw) >= 4:
                    for name_part in name_lc.replace("_", " ").replace("-", " ").replace(".", " ").split():
                        similarity = difflib.SequenceMatcher(None, kw.lower(), name_part).ratio()
                        if similarity > 0.8:
                            s += 0.4
                            
            # File type filtering for resume
            if is_resume_search:
                if ext_lc in _resume_preferred_exts:
                    s += 0.5
                elif ext_lc in _resume_avoid_exts:
                    s -= 1.0 # Heavy penalty for images
            
            new_scored_results.append((s, r))

        # Re-rank: exact boost files first (sorted by original score), then rest
        ranked = sorted(new_scored_results, key=lambda x: (-1 if x[1].get("_exact_boost") else 0, -x[0]))

        # ── Relevance threshold + keyword boosting ───────────────────────────
        _boost_keywords = ["feedback", "customer", "resume"]
        
        # Figure out which boost keywords are present in the query (fuzzy)
        active_boost_keywords = []
        for k in _boost_keywords:
            for q_word in query.lower().split():
                if difflib.SequenceMatcher(None, q_word, k).ratio() >= 0.75:
                    active_boost_keywords.append(k)
                    break

        final_scored_results = []
        for s, r in ranked[:limit]:
            name_lc = (r.get("name") or "").lower()
            if any(k in name_lc for k in active_boost_keywords):
                s += 0.5  # boost score for exact keyword in name
            final_scored_results.append((s, r))
        
        scored_results = sorted(final_scored_results, key=lambda x: -x[0])
        
        _RELEVANCE_THRESHOLD = 0.15
        _STRONG_MATCH_THRESHOLD = 0.65  # High threshold for auto-select
        
        above_threshold = [(s, r) for s, r in scored_results if s >= _RELEVANCE_THRESHOLD]
        
        if not above_threshold:
            print("[SEARCH] filtered results count: 0")
            return []
            
        print(f"[SEARCH] filtered results count: {len(above_threshold)}")
        
        # ── Auto-selection ONLY if > high threshold ────────────────────────
        best_score = above_threshold[0][0]
        
        if best_score >= _STRONG_MATCH_THRESHOLD and len(above_threshold) == 1:
            results = [above_threshold[0][1]]
            log.info("[FileSearch] Strong-match auto-select → 1 result (score=%.3f)", best_score)
        else:
            results = [r for _, r in above_threshold[:5]]
        

        # When the user didn't ask for images, push images to the end
        if not _query_requests_images:
            text_results = [
                r for r in results
                if (r.get("extension") or "").lower() not in _IMAGE_EXTS
            ]
            image_results = [
                r for r in results
                if (r.get("extension") or "").lower() in _IMAGE_EXTS
            ]
            results = text_results + image_results

        # Clean up internal keys before returning
        for r in results:
            r.pop("_exact_boost", None)

        return results

    def search_by_extension(
        self, ext: str, limit: int = _DEFAULT_LIMIT
    ) -> list[dict]:
        """Return all indexed files with the given extension (e.g. ``'.pdf'``)."""
        try:
            from services.db_service import get_connection
            ext_lc = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT path, name, extension, size_bytes, mtime "
                    "FROM files WHERE extension = ? "
                    "ORDER BY mtime DESC LIMIT ?",
                    (ext_lc, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.warning("[FileSearch] search_by_extension(%r) failed: %s", ext, exc)
            return []

    def list_all(
        self, limit: int = 50, folder_prefix: str | None = None
    ) -> list[dict]:
        """Return up to *limit* recently-indexed files ordered by mtime desc.

        When *folder_prefix* is given only files inside that directory are
        returned, so FILE_LIST respects the user's current folder context.
        """
        try:
            from services.db_service import list_files
            return list_files(limit=limit, folder_prefix=folder_prefix)
        except Exception as exc:
            log.warning("[FileSearch] list_all() failed: %s", exc)
            return []

    # ── Formatting ───────────────────────────────────────────────────────────

    @staticmethod
    def format_listing(
        results: list[dict],
        header: str = "",
        show_folder: bool = True,
    ) -> str:
        """Format *results* as a numbered list for the conversational UI.

        Parameters
        ----------
        results : list[dict]
            Each dict must have at least ``name`` and ``path``.
        header : str
            Optional sentence prepended before the list.  Auto-generated
            when empty.
        show_folder : bool
            When True, appends the parent folder in parentheses after each
            filename so the user can distinguish same-named files.

        Returns
        -------
        str
            Multi-line string, e.g.::

                I found 3 files:
                1. Sandeep_Resume.pdf  (C:\\Users\\...\\Documents)
                2. Resume_2024.docx   (C:\\Users\\...\\Downloads)
                3. Updated_Resume.txt (C:\\Users\\...\\Desktop)

                Which one do you want to open or summarize?
        """
        if not results:
            return "No matching files found."

        if not header:
            n = len(results)
            if n == 1:
                header = "Found 1 highly relevant file:"
            elif n <= 3:
                header = f"Found {n} relevant files:"
            else:
                header = f"I found {n} files:"

        lines = [header]
        for i, r in enumerate(results, start=1):
            raw_name = r.get("name") or os.path.basename(r.get("path", "?"))
            # Restore original capitalisation from path when name is lower-cased
            path_val = r.get("path", "")
            display_name = os.path.basename(path_val) if path_val else raw_name
            folder = os.path.dirname(path_val) if path_val else ""
            hint = f"  ({folder})" if (show_folder and folder) else ""
            lines.append(f"{i}. {display_name}{hint}")

        if len(results) > 1:
            lines.append("\nWhich one would you like to open or summarize?")

        return "\n".join(lines)

    # ── Selection resolution ─────────────────────────────────────────────────

    def resolve_selection(
        self,
        text: str,
        candidates: list[dict],
    ) -> Optional[str]:
        """Resolve a user's reply to a file path from *candidates*.

        Supported formats
        -----------------
        - Digit          : ``"1"``, ``"2"``
        - Ordinal word   : ``"first"``, ``"second one"``, ``"the third"``
        - Filename/stem  : ``"resume.pdf"``, ``"Sandeep_Resume"``
        - ``"last one"`` : returns the last candidate

        Returns
        -------
        str or None
            Absolute path string when resolved, ``None`` otherwise.
        """
        if not candidates:
            return None

        t = text.strip().lower()

        # "last" / "last one"
        if re.search(r"\blast\b", t):
            return candidates[-1]["path"]

        # Pure digit
        m = re.match(r"^\s*(\d+)\s*$", t)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]["path"]
            return None  # out of range

        # Ordinal word embedded in text ("second one", "the third file")
        for word, idx in _ORDINALS.items():
            if re.search(rf"\b{re.escape(word)}\b", t):
                if 1 <= idx <= len(candidates):
                    return candidates[idx - 1]["path"]

        # Filename or stem substring match
        for r in candidates:
            raw_name = (r.get("name") or "").lower()
            stem = os.path.splitext(raw_name)[0]
            if raw_name and (raw_name in t or (len(stem) > 2 and stem in t)):
                return r["path"]
            # Also check display name from path
            path_base = os.path.basename(r.get("path", "")).lower()
            path_stem = os.path.splitext(path_base)[0]
            if path_base and (path_base in t or (len(path_stem) > 2 and path_stem in t)):
                return r["path"]

        return None

    # ── Index-health check ───────────────────────────────────────────────────

    @staticmethod
    def is_index_empty() -> bool:
        """Return True when the file index has no rows (no folder granted yet)."""
        try:
            from services.db_service import get_connection
            with get_connection() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()
            return (row["n"] if row else 0) == 0
        except Exception:
            return True


# Module-level singleton
file_search_service = FileSearchService()
