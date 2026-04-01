# Semantic Email Search Implementation Guide

## Overview

Upgrade your email search from keyword/fuzzy matching to **semantic similarity search** using:
- **Sentence Transformers** (embeddings)
- **ChromaDB** (vector storage)
- **Existing modular architecture** (services, agents, tools)

---

## Architecture Design

```
┌─────────────────────────────────────────────────────────────┐
│                      User Query                              │
└────────────────────────┬────────────────────────────────────┘
                         │
         ╔═══════════════╩═══════════════╗
         ▼                               ▼
    [Email Search Tool]            [Email Agent]
    (agents/knowledge/)            (agents/tasks/)
         │                               │
         └───────────┬───────────────────┘
                     ▼
        ┌─────────────────────────────┐
        │  Email Retrieval Agent      │  ← NEW
        │  (semantic search)          │
        └────────────┬────────────────┘
                     │
         ╔═══════════╩═══════════════╗
         ▼                           ▼
    Text Embedding           Vector Store
    (Sentence Trans)         (ChromaDB)
         │                       │
         └──────────┬───────────┘
                    ▼
            ┌──────────────────┐
            │  Email Cache     │
            │ (emails.json)    │
            └──────────────────┘
```

---

## File Structure

```
local_ai_assistant/
├── services/
│   ├── vector_store_service.py          (existing - documents)
│   ├── email_vector_store_service.py    (NEW - emails)
│   ├── email_sync_service.py            (NEW - periodic sync)
│   └── __init__.py
├── agents/
│   └── knowledge/
│       ├── email_retrieval_agent.py     (NEW - semantic search)
│       ├── email_query_agent.py         (UPDATED - use semantic + keyword)
│       └── retrieval_agent.py           (existing - documents)
├── engines/
│   ├── embedding_engine.py              (NEW - shared embedding logic)
│   └── rag_engine.py                    (existing)
├── data/
│   ├── emails.json                      (existing)
│   ├── email_cache.json                 (existing)
│   └── vector_store_emails/             (NEW - ChromaDB persistence)
└── configs/
    └── settings.py                      (UPDATE - add email vector config)
```

---

## Implementation Steps

### Step 1: Set up Embedding Engine (Shared)
Create `engines/embedding_engine.py` with reusable embedding utilities.

### Step 2: Create Email Vector Store Service
Similar to `vector_store_service.py` but for emails:
- Builds embeddings from email subject + body
- Stores in ChromaDB (separate from docs)
- Lazy background loading
- Incremental updates

### Step 3: Create Email Retrieval Agent
Semantic search logic:
- Convert user query to embedding
- Similarity search in ChromaDB
- Rerank by relevance
- Return top N emails

### Step 4: Update Email Query Agent
Integrate both:
- Try semantic search first (if store ready)
- Fall back to keyword search
- Hybrid scoring

### Step 5: Add Email Sync Service (Optional)
Periodic background sync:
- Fetch new emails every N hours
- Increment embeddings (don't rebuild)
- Update ChromaDB

---

## Key Differences from Document Search

| Aspect | Documents | Emails |
|--------|-----------|--------|
| **Chunking** | Large docs → multiple chunks | Each email = 1 document |
| **Metadata** | file_name, source, page | sender, date, subject, id |
| **Update** | Rebuild on new files | Incremental (new emails only) |
| **Embedding** | Full content | Subject + body (optimized) |
| **Search** | Broad + specific | Specific (find emails) |

---

## Configuration Example

```python
# configs/settings.py additions
class Settings:
    # Email Vector Store
    email_vector_store_path: str = "data/vector_store_emails"
    email_embedding_model: str = "all-MiniLM-L6-v2"
    email_sync_interval_hours: int = 6
    email_chunk_size: int = 2000  # chars per email chunk
    email_similarity_threshold: float = 0.4
    semantic_search_k: int = 10   # top-k results
    hybrid_semantic_weight: float = 0.7  # 70% semantic, 30% keyword
```

---

## Performance Considerations

1. **Embedding Model:**
   - Use `all-MiniLM-L6-v2` (lightweight, good quality)
   - Or `all-mpnet-base-v2` (better quality, slower)

2. **Batch Processing:**
   - Embed 50-100 emails at once (faster than individual)
   - Use GPU if available (10x speedup)

3. **Storage:**
   - ChromaDB auto-compresses: ~1KB per email embedding
   - 10,000 emails ≈ 10MB

4. **Search Speed:**
   - Semantic: 10-50ms (pure vector)
   - Keyword: <10ms (string matching)
   - Hybrid: 50-100ms (combined)

---

## Sync Strategy

### Option A: Incremental Sync (Recommended)
```python
# Every 6 hours
1. Fetch emails newer than last_sync_time
2. Embed only new emails
3. Add to ChromaDB (don't rebuild)
4. Update manifest.json with timestamp
```

**Pros:** Fast, no reindexing
**Cons:** Requires careful state management

### Option B: Full Rebuild (Simple)
```python
# Every 24 hours (off-peak)
1. Fetch ALL emails
2. Clear old ChromaDB
3. Rebuild from scratch
4. Run at 2 AM (background task)
```

**Pros:** Simple, no state issues
**Cons:** Slow for large inboxes, temporary downtime

### Recommended: Hybrid
- **Daily:** Incremental sync
- **Weekly:** Full rebuild (cleanup, fix drift)

---

## Testing Checklist

- [ ] Email fetching works (IMAP connectivity)
- [ ] Embeddings generated correctly (~384 dims for MiniLM)
- [ ] ChromaDB stores and retrieves embeddings
- [ ] Semantic search returns relevant emails
- [ ] Hybrid scoring balances semantic + keyword
- [ ] Fallback to keyword when semantic fails
- [ ] Handles 100+ emails without slowdown
- [ ] Handles new emails (incremental sync)
- [ ] Metadata preserved correctly (sender, date, etc.)
- [ ] Handles special chars and non-ASCII properly

---

## Next Steps

1. ✅ Create `engines/embedding_engine.py`
2. ✅ Create `services/email_vector_store_service.py`
3. ✅ Create `agents/knowledge/email_retrieval_agent.py`
4. ✅ Update `agents/knowledge/email_query_agent.py`
5. ⏭️ (Optional) Create `services/email_sync_service.py`
6. ⏭️ Update `configs/settings.py`
7. ⏭️ Integration tests
8. ⏭️ Deploy and monitor performance

See implementation files below for complete code.
