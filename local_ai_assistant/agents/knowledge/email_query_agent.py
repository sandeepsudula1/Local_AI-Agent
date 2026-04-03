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

    # ----------------------------------------------------------------
    # STEP 1: Detect a sender filter
    # Patterns handled:
    #   "from sandeep"            →  from_match
    #   "from sandeep only"       →  from_match (trailing "only" ignored)
    #   "sandeep mails/emails"    →  name_before_mail
    #   "only sandeep mails"      →  name_before_mail
    #   "summarize only sandeep mails" → name_before_mail
    # ----------------------------------------------------------------
    sender_filter = None

    # Pattern 1: explicit "from <name>" anywhere in query
    _from_pat = re.search(r"\bfrom\s+([\w\.@\-]+)", q)
    if _from_pat:
        sender_filter = _from_pat.group(1).strip()

    # Pattern 2: "<name> mails/emails" or "only <name> mails/emails"
    # (captures the word immediately before mails/emails that isn't a stopword)
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

    # Pattern 3: "only <name>" without the word mails — e.g. "show only susmitha"
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
            # Direct substring: "manikanta" in "bharath vamsi manikanta reddy"
            if sf in ef:
                return True
            # Split by spaces / angle-brackets / commas to get name parts
            name_part = ef.split("<")[0].strip()
            # Word-by-word fuzzy: any token in the full name is similar to sf
            if any(_similar(sf, tok) > 0.65 for tok in re.split(r"[\s,]+", name_part) if tok):
                return True
            # Full-name fuzzy: last resort for short/typo queries
            if _similar(sf, name_part) > 0.65:
                return True
            return False

        filtered = [e for e in emails if _sender_matches(e.get("from", ""))]
        # If name matched at least one email, restrict to those; otherwise ignore the filter
        if filtered:
            emails = filtered
            # If the query is purely about the sender (no topic tokens), return top-N sorted by id
            topic_q = re.sub(r"\b(from|only|mails?|emails?|summarize|show|give|list|get|find|all|"\
                             r"recent|latest|new|my|your)\b", "", q).strip()
            topic_q = re.sub(r"\b" + re.escape(sender_filter) + r"\b", "", topic_q).strip()
            if not topic_q or len(topic_q) < 3:
                # Pure sender query — return all their emails, sorted by id descending
                return sorted(emails, key=lambda e: int(str(e.get("id", 0) or 0)), reverse=True)[:max_results]

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
    # Remove the sender name from tokens so it doesn't corrupt topic scoring
    if sender_filter:
        tokens = [t for t in tokens if t != sender_filter.lower()]

    # Synonym expansion — map query words to related terms to check in emails
    _SYNONYMS = {
        "immediate":    ["immediate","urgent","emergency","asap","critical","soon","quickly"],
        "immediately":  ["immediate","urgent","emergency","asap","critical","soon","quickly"],
        "urgent":       ["urgent","emergency","asap","immediate","critical","important"],
        "emergency":    ["emergency","urgent","critical","immediate","asap"],
        "respond":      ["respond","response","reply","answer","revert"],
        "response":     ["respond","response","reply","answer","revert"],
        "responce":     ["respond","response","reply","answer","revert"],   # typo
        "reply":        ["reply","respond","response","answer"],
        "meeting":      ["meeting","meet","conference","call","discussion","sync"],
        "job":          ["job","offer","position","role","employment","work","career","hiring"],
        "interview":    ["interview","screening","candidature","selection"],
        "timesheet":    ["timesheet","time sheet","time-sheet","attendance","hours","timesheets"],
        "timesheets":   ["timesheet","time sheet","time-sheet","attendance","hours","timesheets"],
        "attendance":   ["attendance","timesheet","time sheet","present","absent"],
        "leave":        ["leave","absence","vacation","holiday","time off","pto"],
        "update":       ["update","status","progress","follow up","followup"],
        "followup":     ["follow up","followup","check in","update","reminder"],
        "follow":       ["follow up","followup","check in","update"],
        "receive":      ["receive","received","got","obtain"],
        "recieve":      ["receive","received","got","obtain"],   # typo
        "recieved":     ["receive","received","got"],            # typo
    }

    def _expanded_tokens(t):
        """Return the token plus any synonyms for it."""
        return _SYNONYMS.get(t, [t])

    for mail in emails:
        subj = (mail.get("subject") or "").lower()
        body = (mail.get("body") or "").lower()
        frm  = (mail.get("from") or "").lower()

        score = 0.0

        # ---- explicit about:/subject: ----
        target_topic = None
        if about_match:
            target_topic = about_match.group(1).strip()
        elif subject_match:
            target_topic = subject_match.group(1).strip()
        if target_topic:
            tt = target_topic.lower()
            if tt in subj:
                score += 2.0
            elif tt in body:
                score += 1.5
            elif _similar(tt, subj) > 0.7 or _similar(tt, body) > 0.7:
                score += 1.0

        # ---- exact full-query substring in subject/body ----
        if q in subj:
            score += 2.0
        elif q in body:
            score += 1.5

        # ---- token keyword matching (primary signal) ----
        token_hits = 0
        for t in tokens:
            check_terms = _expanded_tokens(t)
            hit_subj = any(term in subj for term in check_terms)
            hit_body = any(term in body for term in check_terms)
            hit_frm  = any(term in frm  for term in check_terms)
            # Lower fuzzy threshold for short tokens to catch near-typos
            fuzzy_thresh = 0.65 if len(t) <= 6 else 0.8
            hit_fuzzy = _similar(t, subj) > fuzzy_thresh

            if hit_subj:
                score += 1.0
                token_hits += 1
            elif hit_body:
                score += 0.6
                token_hits += 1
            elif hit_frm:
                score += 0.4
                token_hits += 1
            elif hit_fuzzy:
                score += 0.5
                token_hits += 1

        # ---- fuzzy similarity of full query: ONLY use when no tokens present ----
        # (avoids irrelevant emails sneaking in via low fuzzy scores)
        if not tokens:
            sim = max(_similar(q, subj), _similar(q, body[:200]), _similar(q, frm))
            score += sim * 1.5

        # If we have tokens and zero token hits, this email is irrelevant — skip it
        if tokens and token_hits == 0:
            continue

        if score > 0:
            scores.append((score, mail))

    # Sort by relevance; no min_score needed — token gate above already filtered noise
    scores.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scores[:5]]



# Backwards-compatible alias
def search_emails_by_text(keyword):
    return improved_search_emails(keyword)


# ---------------------------------------------------------------------------
# Natural-language date helpers
# ---------------------------------------------------------------------------

_MONTH_ABBR = {
    "jan": "jan", "january": "jan",
    "feb": "feb", "february": "feb",
    "mar": "mar", "march": "mar",
    "apr": "apr", "april": "apr",
    "may": "may",
    "jun": "jun", "june": "jun",
    "jul": "jul", "july": "jul",
    "aug": "aug", "august": "aug",
    "sep": "sep", "september": "sep",
    "oct": "oct", "october": "oct",
    "nov": "nov", "november": "nov",
    "dec": "dec", "december": "dec",
}

_DATE_PAT1 = re.compile(
    r'\b(\d{1,2})\s+'
    r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may'
    r'|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?'
    r'|nov(?:ember)?|dec(?:ember)?)\s+(\d{4})\b',
    re.IGNORECASE,
)
_DATE_PAT2 = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may'
    r'|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?'
    r'|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2}),?\s+(\d{4})\b',
    re.IGNORECASE,
)


def _extract_date_parts(q: str):
    """Return (day_str, month_abbr, year_str) parsed from *q*, or None."""
    m = _DATE_PAT1.search(q)
    if m:
        day = str(int(m.group(1)))  # strip leading zero for matching
        mon = _MONTH_ABBR[m.group(2).lower()[:3]]
        return day, mon, m.group(3)
    m = _DATE_PAT2.search(q)
    if m:
        mon = _MONTH_ABBR[m.group(1).lower()[:3]]
        day = str(int(m.group(2)))
        return day, mon, m.group(3)
    return None


_RECENT_WORDS = {"recent", "recently", "latest", "newest"}


def _is_recent_query(q: str) -> bool:
    return bool(set(re.findall(r'\w+', q.lower())) & _RECENT_WORDS)


_RELATIVE_DATE_RE = re.compile(
    r'\b(today|yesterday|last\s+week|this\s+week|last\s+month|this\s+month)\b',
    re.IGNORECASE,
)


def _get_relative_date_range(q: str):
    """Return (start_date, end_date) for relative date expressions, or None."""
    m = _RELATIVE_DATE_RE.search(q)
    if not m:
        return None
    expr = m.group(1).lower().replace("  ", " ")
    today = date.today()
    if expr == "today":
        return today, today
    if expr == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if expr == "last week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    if expr == "this week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if expr == "last month":
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end
    if expr == "this month":
        return today.replace(day=1), today
    return None


def _email_in_date_range(email_date_str: str, start: date, end: date) -> bool:
    """Return True if the email's date falls within [start, end]."""
    if not email_date_str:
        return False
    # Try RFC 2822 parsing first (e.g. "Mon, 23 Mar 2026 14:57:16 +0530")
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(email_date_str)
        return start <= dt.date() <= end
    except Exception:
        pass
    # Fallback: brute string matching using day + 3-letter month + year
    dl = email_date_str.lower()
    for d in (start + timedelta(n) for n in range((end - start).days + 1)):
        day_s = str(d.day)
        mon_s = d.strftime("%b").lower()
        year_s = str(d.year)
        if day_s in dl and mon_s in dl and year_s in dl:
            return True
    return False


def _format_email_results(results: list, query: str, max_chars: int = 160) -> str:
    if not results:
        return f"No matching emails found for: {query}"
    lines = [f"Found {len(results)} email(s) for: {query}", ""]
    for mail in results:
        subject = mail.get("subject") or "(no subject)"
        sender = mail.get("from") or "Unknown"
        mail_id = mail.get("id", "N/A")
        date_str = mail.get("date", "")
        body = (mail.get("body") or "").strip().replace("\n", " ")
        if len(body) > max_chars:
            body = body[: max_chars - 3] + "..."
        date_part = f" | Date: {date_str}" if date_str else ""
        lines.append(f"- [{mail_id}] From: {sender} | Subject: {subject}{date_part}")
        if body:
            lines.append(f"  {body}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Primary entry point (called by tool_executor)
# ---------------------------------------------------------------------------

def handle_email_query(query: str, max_results: int = 10) -> str:
    """Resolve an EMAIL_SEARCH query with date, recency, or keyword logic."""
    q = (query or "").strip().lower()
    emails = load_all_emails()
    try:
        emails_sorted = sorted(
            emails,
            key=lambda e: int(str(e.get("id", 0) or 0)),
            reverse=True,
        )
    except Exception:
        emails_sorted = list(emails)

    # ── "When did I receive mail from X" query ───────────────────────────────
    if "when" in q:
        _when_pat = re.search(
            r"\b(?:from|by)\s+([\w][\w\s]{1,40}?)(?:\s*\?|$|\s+(?:send|sent|mail|email))",
            q,
        ) or re.search(
            r"\bwhen\b.{0,30}\bfrom\s+([\w][\w\s]{1,40}?)(?:\s*\?|$)",
            q,
        )
        sender_q = _when_pat.group(1).strip() if _when_pat else None
        # Also try pattern: "when did I get/receive <name> mail"
        if not sender_q:
            _when_pat2 = re.search(
                r"\b([\w][\w\s]{2,30}?)\s+(?:mails?|emails?)\b",
                q,
            )
            if _when_pat2:
                candidate = _when_pat2.group(1).strip()
                _WHEN_STOP = {"my", "all", "any", "new", "recent", "latest", "a", "the"}
                if candidate not in _WHEN_STOP and len(candidate) > 2:
                    sender_q = candidate
        if sender_q and len(sender_q) > 2:
            sf = sender_q.lower()
            matched = [
                e for e in emails_sorted
                if sf in (e.get("from") or "").lower()
                or any(
                    _similar(part, sf) > 0.65
                    for part in re.split(r"[\s<>@,]+", (e.get("from") or "").lower())
                    if part
                )
            ]
            if matched:
                latest = matched[0]  # sorted by id desc → newest first
                date_str = latest.get("date", "unknown date")
                from_str = latest.get("from", sender_q)
                return (
                    f"The latest email from {from_str} was received on {date_str}.\n\n"
                    + _format_email_results(matched[:max_results], query)
                )
            return f"No emails found from '{sender_q}'."

    # ── Relative date (yesterday / today / last week …) ─────────────────
    date_range = _get_relative_date_range(q)
    if date_range:
        start_d, end_d = date_range
        matched = [m for m in emails_sorted if _email_in_date_range(m.get("date", ""), start_d, end_d)]
        if matched:
            return _format_email_results(matched[:max_results], query)
        return f"No emails found for: {query} (checked {start_d} to {end_d})"

    # ── Absolute date-filtered query ─────────────────────────────────────
    date_parts = _extract_date_parts(q)
    if date_parts:
        day, mon, year = date_parts
        matched = [
            m for m in emails_sorted
            if day in (m.get("date") or "").lower()
            and mon in (m.get("date") or "").lower()
            and year in (m.get("date") or "").lower()
        ]
        if matched:
            return _format_email_results(matched[:max_results], query)
        # Fall through to keyword search when nothing matches the date

    # ── "Recently received" / "latest" query ────────────────────────────
    if _is_recent_query(q):
        return _format_email_results(emails_sorted[:max_results], query)

    # ── Hybrid keyword/semantic search ───────────────────────────────────
    # Tries semantic search first (if available), then keyword search
    results = hybrid_email_search(query, max_results=max_results)
    return _format_email_results(results, query)