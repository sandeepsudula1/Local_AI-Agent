import os
import json
import re
import time
import logging
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger(__name__)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
try:
    from configs.settings import DATA_DIR as _DATA_DIR
    EMAIL_FILE = os.path.join(str(_DATA_DIR), "emails.json")
    CACHE_FILE = os.path.join(str(_DATA_DIR), "email_cache.json")
except Exception:
    EMAIL_FILE = os.path.join(_PROJECT_ROOT, "data", "emails.json")
    CACHE_FILE = os.path.join(_PROJECT_ROOT, "data", "email_cache.json")

# In-memory TTL cache — avoids double IMAP fetches within the same query
_live_cache: list = []
_live_cache_ts: float = 0.0
_LIVE_CACHE_TTL: float = 10.0  # seconds


def _fetch_live_and_update_cache(force: bool = False) -> list:
    """Fetch latest emails from IMAP, merge into cache file, return all.

    Results are cached in memory for _LIVE_CACHE_TTL seconds so that
    multiple calls within the same query cycle only hit IMAP once.
    Pass force=True to bypass the TTL (e.g. from startup sync).
    """
    global _live_cache, _live_cache_ts

    now = time.time()
    if not force and _live_cache and (now - _live_cache_ts) < _LIVE_CACHE_TTL:
        return _live_cache  # return cached result — no IMAP round-trip

    try:
        import sys
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)

        from agents.tasks.email_agent import EmailAgent
        agent = EmailAgent()
        if not getattr(agent, 'available', True):
            print("[Email] EmailAgent unavailable (check credentials/.env)")
            return _live_cache  # return whatever we had

        live = agent.fetch_recent_emails(last_n=200)
        if live:
            agent.save_to_cache(live)
            _live_cache = live
            _live_cache_ts = time.time()
            return live
        return _live_cache  # IMAP returned nothing - keep previous
    except Exception as _e:
        print(f"[Email] IMAP fetch error: {_e}")
        return _live_cache  # return whatever was cached rather than empty


def load_all_emails():
    """Always fetch live from IMAP, merge with disk cache, deduplicate by ID."""
    emails_map = {}  # id -> email dict, deduped

    # 1. Load static emails.json
    if os.path.exists(EMAIL_FILE):
        try:
            with open(EMAIL_FILE, "r", encoding="utf-8") as f:
                for e in json.load(f):
                    emails_map[str(e.get("id", ""))] = e
        except Exception:
            pass

    # 2. Load cache file
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for e in data.get("emails", []):
                    emails_map[str(e.get("id", ""))] = e
        except Exception:
            pass

    # 3. Fetch live from IMAP — respects the in-memory TTL (default 30s).
    # To force an immediate refresh, call invalidate_email_cache() first.
    for e in _fetch_live_and_update_cache():
        emails_map[str(e.get("id", ""))] = e

    return list(emails_map.values())


def invalidate_email_cache():
    """Force the next load_all_emails() call to do a fresh IMAP fetch."""
    global _live_cache, _live_cache_ts
    _live_cache = []      # wipe stale in-memory data
    _live_cache_ts = 0.0  # reset TTL so next call always hits IMAP


def _similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def hybrid_email_search(query: str, max_results: int = 20) -> list:
    """
    Hybrid search combining semantic + keyword matching.
    
    First attempts semantic search using embeddings, then falls back to keyword
    search. Results are merged with weighted scoring:
    - Semantic: 70% weight
    - Keyword: 30% weight
    """
    results_by_id = {}
    
    # Try semantic search first (if vector store is ready)
    try:
        from agents.knowledge.email_retrieval_agent import semantic_email_search
        from services.email_vector_store_service import get_email_vector_store_service
        
        store = get_email_vector_store_service()
        if store.is_ready:
            log.debug("Attempting semantic search for: %s", query)
            semantic_results = semantic_email_search(query, top_k=max_results, threshold=0.3)
            
            for result in semantic_results:
                email_id = result.get("id", "")
                if email_id:
                    # Store semantic score and metadata
                    results_by_id[email_id] = {
                        "score": result.get("score", 0.0),
                        "metadata": {
                            "sender": result.get("sender", ""),
                            "subject": result.get("subject", ""),
                            "date": result.get("date", ""),
                        },
                        "semantic_score": result.get("score", 0.0),
                        "keyword_score": 0.0,
                    }
                    log.debug("Semantic match: %s (score=%.3f)", result.get("subject"), result.get("score"))
        else:
            log.debug("Email vector store not ready for semantic search")
    except Exception as e:
        log.debug("Semantic search unavailable: %s", e)
    
    # Always do keyword search (as fallback or complement)
    keyword_results = improved_search_emails(query, max_results=max_results, use_semantic=False)
    
    # Merge keyword results
    for result in keyword_results:
        email_id = str(result.get("id", ""))
        keyword_score = 1.0 if keyword_results else 0.0
        
        if email_id in results_by_id:
            # Hybrid score: 70% semantic + 30% keyword
            results_by_id[email_id]["keyword_score"] = keyword_score
            semantic_score = results_by_id[email_id]["semantic_score"]
            results_by_id[email_id]["score"] = (0.7 * semantic_score) + (0.3 * keyword_score)
        else:
            # Keyword-only result
            results_by_id[email_id] = {
                "score": keyword_score * 0.3,  # Downweight keyword-only results
                "semantic_score": 0.0,
                "keyword_score": keyword_score,
                "metadata": {
                    "sender": result.get("from", ""),
                    "subject": result.get("subject", ""),
                    "date": result.get("date", ""),
                },
            }
            result["semantic_score"] = 0.0  # Mark for awareness
    
    # Convert back to email dicts and sort by score
    merged_results = []
    for email_id, score_data in results_by_id.items():
        # Find original email to return complete dict
        emails = load_all_emails()
        for email in emails:
            if str(email.get("id", "")) == email_id:
                merged_results.append(email)
                break
    
    # Sort by hybrid score (we'd need to track it, so sort by keyword approach)
    return merged_results[:max_results]


def improved_search_emails(query, max_results=20, use_semantic=True):
    """Robust natural-language email search with fuzzy matching.

    Supports:
      - Hard sender filtering: "from sandeep", "sandeep mails", "only susmitha"
      - Topic search: "emergency meeting", "job offer"
      - Combined: "meeting from susmitha", "job mail from kutluri"
    """
    q = (query or "").strip().lower()
    # Normalize: collapse spaced compound words to joined form so "time sheet"
    # and "timesheet" both tokenise to the same token for synonym expansion.
    _COMPOUND_JOINS = [
        (re.compile(r"\btime\s+sheet\b"),   "timesheet"),
        (re.compile(r"\bfollow\s+up\b"),    "followup"),
        (re.compile(r"\bcheck\s+in\b"),     "checkin"),
        (re.compile(r"\btime\s+off\b"),     "timeoff"),
    ]
    for _pat, _rep in _COMPOUND_JOINS:
        q = _pat.sub(_rep, q)
    emails = load_all_emails()
    if not q:
        return []

    # PART 4: EMAIL CONTEXT LOCK — Check for active context
    from memory.conversation_memory import conversation_memory
    active = conversation_memory.get_active_email()
    # If query is short follow-up and we have an active email, use it
    if active and len(q.split()) < 4:
        active["confidence"] = 100
        return [active]

    # ----------------------------------------------------------------
    # STEP 1: Detect a sender filter
    # ----------------------------------------------------------------
    sender_filter = None

    # Pattern 1: explicit "from <name>" anywhere in query
    _from_pat = re.search(r"\bfrom\s+([\w\.@\-]+)", q)
    if _from_pat:
        sender_filter = _from_pat.group(1).strip()

    # Pattern 2: "<name> mails/emails" or "only <name> mails/emails"
    _SENDER_STOP = {
        "all","my","your","his","her","their","the","a","an","some","any",
        "new","recent","latest","old","unread","sent","received","summarize",
        "get","show","give","list","find","check","read","only","just","me",
        "mail","email","inbox","mails","emails"
    }
    if not sender_filter:
        _name_pat = re.search(r"\b([\w]+)\s+(?:mails?|emails?)\b", q)
        if _name_pat:
            candidate = _name_pat.group(1).strip()
            if candidate not in _SENDER_STOP and len(candidate) > 2:
                sender_filter = candidate

    # Pattern 3: "only <name>" without the word mails
    if not sender_filter:
        _only_pat = re.search(r"\bonly\s+([\w]+)\b", q)
        if _only_pat:
            candidate = _only_pat.group(1).strip()
            if candidate not in _SENDER_STOP and len(candidate) > 2:
                sender_filter = candidate

    # ----------------------------------------------------------------
    # STEP 2: Apply sender hard-filter if detected
    # ----------------------------------------------------------------
    if sender_filter:
        sf = sender_filter.lower()

        def _sender_matches(email_from: str) -> bool:
            ef = (email_from or "").lower()
            if sf in ef:
                return True
            name_part = ef.split("<")[0].strip()
            if any(_similar(sf, tok) > 0.65 for tok in re.split(r"[\s,]+", name_part) if tok):
                return True
            if _similar(sf, name_part) > 0.65:
                return True
            return False

        filtered = [e for e in emails if _sender_matches(e.get("from", ""))]
        if filtered:
            emails = filtered
            topic_q = re.sub(r"\b(from|only|mails?|emails?|summarize|show|give|list|get|find|all|"\
                             r"recent|latest|new|my|your)\b", "", q).strip()
            topic_q = re.sub(r"\b" + re.escape(sender_filter) + r"\b", "", topic_q).strip()
            if not topic_q or len(topic_q) < 3:
                results = sorted(emails, key=lambda e: int(str(e.get("id", 0) or 0)), reverse=True)[:max_results]
                for r in results: r["confidence"] = 100
                return results

    # ----------------------------------------------------------------
    # STEP 3: Normal keyword + fuzzy scoring on the (possibly filtered) list
    # ----------------------------------------------------------------
    about_match = re.search(r"\babout\s+(.+)$", q)
    subject_match = re.search(r"\bsubject[:\s]+(.+)$", q)

    scores = []

    _STOP = {
        "the","a","an","is","in","it","of","to","and","or","for",
        "from","my","me","we","all","any","get","do","did","are",
        "was","be","on","at","by","with","that","this","but","not",
        "can","has","have","had","will","what","which","mail","mails",
        "email","emails","inbox","find","show","related","containing",
        "about","send","sent","received","receive","got","regarding",
        "i","please","just","some","there","recent","recently","newest",
        "new","latest","only","give","summarize","summary",
    }
    tokens = [t for t in re.findall(r"\w+", q) if len(t) > 2 and t not in _STOP]
    if sender_filter:
        tokens = [t for t in tokens if t != sender_filter.lower()]

    _SYNONYMS = {
        "meeting":      ["meeting","meet","conference","call","discussion","sync"],
        "job":          ["job","offer","position","role","employment","work","career","hiring"],
        "timesheet":    ["timesheet","time sheet","time-sheet","attendance","hours","timesheets"],
        "leave":        ["leave","absence","vacation","holiday","time off","pto"],
        "update":       ["update","status","progress","follow up","followup"],
    }

    def _expanded_tokens(t):
        return _SYNONYMS.get(t, [t])

    for mail in emails:
        subj = (mail.get("subject") or "").lower()
        body = (mail.get("body") or "").lower()
        frm  = (mail.get("from") or "").lower()

        score = 0.0

        # Token keyword matching
        token_hits = 0
        for t in tokens:
            check_terms = _expanded_tokens(t)
            hit_subj = any(term in subj for term in check_terms)
            hit_body = any(term in body for term in check_terms)
            
            if hit_subj:
                score += 30.0
                token_hits += 1
            elif hit_body:
                score += 10.0
                token_hits += 1

        if tokens and token_hits == 0:
            continue

        if score > 0:
            confidence = min(100, int(score))
            mail["confidence"] = confidence
            scores.append((score, mail))

    scores.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scores[:5]]


# Backwards-compatible alias
def search_emails_by_text(keyword):
    return improved_search_emails(keyword)


def handle_email_query(query: str, max_results: int = 10) -> str:
    """Resolve an EMAIL_SEARCH query using direct IMAP access."""
    q = (query or "").strip().lower()
    
    try:
        from agents.tasks.email_agent import EmailAgent
        agent = EmailAgent()
        results = agent.search_live_imap(q)
    except Exception as e:
        return f"Error executing IMAP search: {e}"
        
    if not results:
        return f"No matching emails found for: {query}"
        
    # Sort by confidence
    results.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    
    # PART 2: DISAMBIGUATION
    if len(results) > 1:
        lines = [f"I found {len(results)} matches for your search. Which one should I use?", ""]
        for mail in results[:3]:
            subject = mail.get("subject") or "(no subject)"
            sender = mail.get("from") or "Unknown"
            conf = mail.get("confidence", 0)
            lines.append(f"- **{subject}** (from {sender}) — {conf}% confidence")
        return "\n".join(lines)
        
    # Single result: PART 1: RESULT CONFIDENCE
    mail = results[0]
    subject = mail.get("subject") or "(no subject)"
    sender = mail.get("from") or "Unknown"
    date_str = mail.get("date", "")
    snippet = mail.get("snippet", "")
    conf = mail.get("confidence", 0)
    
    date_part = f" | Date: {date_str}" if date_str else ""
    return (
        f"Found match ({conf}% confidence):\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}{date_part}\n\n"
        f"{snippet}"
    )