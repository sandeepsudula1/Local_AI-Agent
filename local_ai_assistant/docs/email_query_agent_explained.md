# `email_query_agent.py` — Full Code Analysis

> File: `agents/knowledge/email_query_agent.py`

---

## Why This File Exists

This is the **query brain for emails**. Before this, email search was done with simple
substring matching — `"amazon" in subject`. That breaks the moment a user types
`"show me urgent mails from kutluri"` — you now have two filters (sender + topic) that
must work together. This file solves that.

---

## Section 1 — Path Setup and Two Data Files

```python
_HERE         = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
EMAIL_FILE    = os.path.join(_PROJECT_ROOT, "data", "emails.json")
CACHE_FILE    = os.path.join(_PROJECT_ROOT, "data", "email_cache.json")
```

Two email sources exist and both must be merged:

| File | Written by | Contains |
|---|---|---|
| `emails.json` | Manually / fixtures | Static test emails |
| `email_cache.json` | `email_agent.py` (IMAP) | Live fetched emails |

**Why two files?**  
During development you want test emails without a live inbox. In production, live emails
arrive from IMAP. Both need to be searchable simultaneously. The merge happens in
`load_all_emails()`.

---

## Section 2 — In-Memory TTL Cache

```python
_live_cache:    list  = []
_live_cache_ts: float = 0.0
_LIVE_CACHE_TTL: float = 10.0  # seconds
```

### The Problem This Solves

In a single user query like _"search emails from Amazon"_, the call chain is:

```
email.search()
  → improved_search_emails()
    → load_all_emails()
      → _fetch_live_and_update_cache()   ← potentially called 3-4× in same request
```

Each call without caching would open a new IMAP TCP connection, wait for the server,
and download 200 emails — costing ~2–5 seconds every time.

With a **10-second TTL (Time-To-Live)**, subsequent calls within the same query cycle
return instantly from RAM:

```python
now = time.time()
if not force and _live_cache and (now - _live_cache_ts) < _LIVE_CACHE_TTL:
    return _live_cache   # no IMAP round-trip
```

`invalidate_email_cache()` is the escape hatch — `smart_agent.py` calls it before
every email query so the user always gets fresh data, bypassing the TTL.

---

## Section 3 — `load_all_emails()` — The Three-Layer Merge

```python
def load_all_emails():
    emails_map = {}   # id → email dict
```

Uses a **dict keyed by email ID** instead of a list. This is the deduplication
mechanism — if the same email appears in all three sources, the last write wins and
you never get duplicates:

```
Layer 1: emails.json        →  emails_map["1042"] = {...}
Layer 2: email_cache.json   →  emails_map["1042"] = {...}   (overwrites)
Layer 3: IMAP live          →  emails_map["1042"] = {...}   (overwrites with freshest)
```

`list(emails_map.values())` collapses the map back into a list for iteration.

---

## Section 4 — `improved_search_emails()` — The Core Algorithm

This is the most complex function. It runs in **three sequential steps**.

---

### Step 1 — Sender Filter Detection (3 Regex Patterns)

```python
# Pattern 1: "from sandeep"
_from_pat  = re.search(r"\bfrom\s+([\w\.@\-]+)", q)

# Pattern 2: "sandeep mails" / "sandeep emails"
_name_pat  = re.search(r"\b([\w]+)\s+(?:mails?|emails?)\b", q)

# Pattern 3: "only susmitha"
_only_pat  = re.search(r"\bonly\s+([\w]+)\b", q)
```

**Why three patterns?** Users say the same thing in different ways:

| User says | Matched by |
|---|---|
| `"emails from sandeep"` | Pattern 1 |
| `"sandeep mails"` | Pattern 2 |
| `"show only susmitha"` | Pattern 3 |

The `_SENDER_STOP` set prevents common words like `"mail"` or `"all"` from being
mistaken for a person's name.

After a sender is identified, **fuzzy matching** handles typos and partial names:

```python
_similar(sf, (e.get("from") or "").lower().split("<")[0].strip()) > 0.65
```

The `.split("<")[0]` strips the email address from `"Sandeep Kumar <sandeep@company.com>"`
to get just the display name `"Sandeep Kumar"` for matching.

If the query is **purely about the sender** (no topic tokens remain), it returns all
their emails sorted by ID descending — most recent first:

```python
return sorted(emails, key=lambda e: int(str(e.get("id", 0) or 0)), reverse=True)[:max_results]
```

---

### Step 2 — Tokenisation with Stop Words + Synonym Expansion

```python
_STOP  = {"the","a","an","is","in","it","of","to","and","or", ...}
tokens = [t for t in re.findall(r"\w+", q) if len(t) > 2 and t not in _STOP]
```

Removes noise words so `"show me all emails about the job offer"` becomes
`["job", "offer"]` after stop-word filtering.

**Synonym expansion** handles intent-rich queries:

```python
_SYNONYMS = {
    "urgent":  ["urgent","emergency","asap","immediate","critical","important"],
    "meeting": ["meeting","meet","conference","call","discussion","sync"],
    "job":     ["job","offer","position","role","employment","work","career"],
}
```

When a token is `"urgent"`, it automatically checks for `emergency`, `asap`, `critical`
in the email body too. Without this, `"find urgent emails"` would miss an email with
subject `"ASAP: Server down"`.

---

### Step 3 — Multi-Signal Scoring

Each email gets a float `score` from several independent signals:

```python
# Exact full-query substring in subject — strongest signal
if q in subj:     score += 2.0
elif q in body:   score += 1.5

# Per-token hits
if hit_subj:      score += 1.0   # subject hit — most reliable
elif hit_body:    score += 0.6   # body hit
elif hit_frm:     score += 0.4   # sender hit
elif hit_fuzzy:   score += 0.5   # close spelling in subject (SequenceMatcher > 0.8)
```

**Why subject hits score higher than body hits?**  
Subjects are written by the sender to summarise the email — they are the
highest-density signal. A match in 10 subject words beats a match buried in 500 words
of body text.

---

### The Anti-Noise Gate

The most important line in the function:

```python
if tokens and token_hits == 0:
    continue   # skip this email entirely
```

Without this, small fuzzy `SequenceMatcher` scores would assign positive values to
completely irrelevant emails, polluting results. This gate says:

> *"If we had searchable tokens and this email matched zero of them — it is irrelevant.
> Do not score it."*

Fuzzy full-query similarity is used **only as a fallback** when there are no tokens
(e.g. a very short query like `"hi"`):

```python
if not tokens:
    sim = max(_similar(q, subj), _similar(q, body[:200]), _similar(q, frm))
    score += sim * 1.5
```

`body[:200]` — only the first 200 characters of the body, because computing
`SequenceMatcher` on a 5000-character email body for every email in the list is
expensive.

---

## The Final Sort

```python
scores.sort(key=lambda x: x[0], reverse=True)
return [m for _, m in scores[:5]]
```

Returns top **5** by relevance score. The sender-filtered path returns up to
`max_results` (default 20). The scoring path returns top 5 because precision matters
more than recall here — better 5 great results than 20 marginal ones.

---

## `_similar()` — Why `SequenceMatcher`

```python
from difflib import SequenceMatcher

def _similar(a, b):
    return SequenceMatcher(None, a, b).ratio()
```

`SequenceMatcher.ratio()` returns a float `0.0–1.0` representing what percentage of
characters are shared in the same order. It handles:

- **Typos:** `"sandep"` → matches `"sandeep"` (ratio ≈ 0.92)
- **Partial names:** `"kutluri"` → matches `"kutluri@company.com"` (ratio ≈ 0.72)

### Threshold Choices

| Use case | Threshold | Reason |
|---|---|---|
| Sender matching | 0.65 | Can be looser — you almost always know who you're looking for |
| Subject fuzzy | 0.80 | Tighter — avoids false positives in noisy subject lines |
| Topic matching | 0.70 | Middle ground |

---

## Data Flow Summary

```
User query: "show me urgent mails from kutluri"
       │
       ▼
Step 1 — Sender detection
       │   regex: "from kutluri"  →  sender_filter = "kutluri"
       │   fuzzy filter: keep only emails where "kutluri" in from field
       │
       ▼
Step 2 — Tokenise remaining query
       │   tokens = ["urgent"]
       │   expand: "urgent" → ["urgent","emergency","asap","critical","immediate"]
       │
       ▼
Step 3 — Score each (filtered) email
       │   "ASAP: Deadline tomorrow" from kutluri  →  score 1.6  ✓
       │   "Hello" from kutluri                    →  token_hits=0 → skipped
       │
       ▼
Sort by score → return top 5
```

---

*Generated from codebase analysis — `agents/knowledge/email_query_agent.py`*
