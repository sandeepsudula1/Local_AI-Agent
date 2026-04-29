"""
services/cache_manager.py
==========================
Unified in-memory LRU cache for extracted text, chunk lists, and
embedding vectors.

Design
------
* Three independent LRU caches are provided:
    - text_cache     : file path → raw extracted text (str)
    - chunk_cache    : file path → list[str]  (chunk texts)
    - embedding_cache: cache key → list[float] (embedding vector)

* Each cache has a configurable capacity (number of entries) and a
  time-to-live (TTL) applied per entry.  Entries older than TTL are
  treated as cache misses and silently evicted.

* Thread-safe via a single lock per cache.

* Provides a ``stats()`` method for observability.

Usage::

    from services.cache_manager import cache_manager

    # Store and retrieve extracted text
    cache_manager.set_text("/path/to/file.txt", "full text content")
    text = cache_manager.get_text("/path/to/file.txt")

    # Store and retrieve chunks
    cache_manager.set_chunks("/path/to/file.pdf", ["chunk1", "chunk2"])
    chunks = cache_manager.get_chunks("/path/to/file.pdf")

    # Invalidate everything for a file (on modification)
    cache_manager.invalidate("/path/to/file.pdf")

    # Usage stats
    print(cache_manager.stats())
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from core.logging_config import get_logger

log = get_logger(__name__)

_DEFAULT_TEXT_CAPACITY      = 50    # files
_DEFAULT_CHUNK_CAPACITY     = 30    # files
_DEFAULT_EMBEDDING_CAPACITY = 200   # individual vectors
_DEFAULT_TEXT_TTL           = 3600  # 1 hour
_DEFAULT_CHUNK_TTL          = 1800  # 30 minutes
_DEFAULT_EMBED_TTL          = 7200  # 2 hours


# ---------------------------------------------------------------------------
# Generic LRU cache
# ---------------------------------------------------------------------------

class _LRUCache:
    """Thread-safe LRU cache with per-entry TTL."""

    def __init__(self, capacity: int, ttl: float, name: str = "cache") -> None:
        self._capacity  = capacity
        self._ttl       = ttl
        self._name      = name
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock      = threading.Lock()
        self._hits      = 0
        self._misses    = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._store:
                self._misses += 1
                return None
            value, ts = self._store[key]
            if time.time() - ts > self._ttl:
                del self._store[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, time.time())
            if len(self._store) > self._capacity:
                evicted_key, _ = self._store.popitem(last=False)
                log.debug("[%s] LRU evicted: %s", self._name, evicted_key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "name"    : self._name,
                "size"    : len(self._store),
                "capacity": self._capacity,
                "hits"    : self._hits,
                "misses"  : self._misses,
                "hit_rate": (
                    round(self._hits / (self._hits + self._misses), 3)
                    if (self._hits + self._misses) > 0 else 0.0
                ),
            }


# ---------------------------------------------------------------------------
# Unified manager
# ---------------------------------------------------------------------------

class CacheManager:
    """Manages separate LRU caches for text, chunks, and embeddings."""

    def __init__(
        self,
        text_capacity: int     = _DEFAULT_TEXT_CAPACITY,
        chunk_capacity: int    = _DEFAULT_CHUNK_CAPACITY,
        embed_capacity: int    = _DEFAULT_EMBEDDING_CAPACITY,
        text_ttl: float        = _DEFAULT_TEXT_TTL,
        chunk_ttl: float       = _DEFAULT_CHUNK_TTL,
        embed_ttl: float       = _DEFAULT_EMBED_TTL,
    ) -> None:
        self._text      = _LRUCache(text_capacity,  text_ttl,  "text_cache")
        self._chunks    = _LRUCache(chunk_capacity, chunk_ttl, "chunk_cache")
        self._embeddings= _LRUCache(embed_capacity, embed_ttl, "embed_cache")

    # ── Text cache ────────────────────────────────────────────────────────────

    def get_text(self, path: str) -> Optional[str]:
        """Return cached full text for *path*, or None on miss/expiry."""
        return self._text.get(path)

    def set_text(self, path: str, text: str) -> None:
        """Store the full extracted text for *path*."""
        self._text.set(path, text)

    # ── Chunk cache ───────────────────────────────────────────────────────────

    def get_chunks(self, path: str) -> Optional[list[str]]:
        """Return cached chunk list for *path*, or None on miss/expiry."""
        return self._chunks.get(path)

    def set_chunks(self, path: str, chunks: list[str]) -> None:
        """Store the chunk list for *path*."""
        self._chunks.set(path, chunks)

    # ── Embedding cache ───────────────────────────────────────────────────────

    def get_embedding(self, key: str) -> Optional[list[float]]:
        """Return a cached embedding vector, or None."""
        return self._embeddings.get(key)

    def set_embedding(self, key: str, vector: list[float]) -> None:
        """Store an embedding vector under an arbitrary *key*."""
        self._embeddings.set(key, vector)

    # ── Invalidation ─────────────────────────────────────────────────────────

    def invalidate(self, path: str) -> None:
        """Remove all cached data for *path* (call when a file is modified)."""
        self._text.delete(path)
        self._chunks.delete(path)
        # Embedding key is typically a query string; eviction is TTL-based only.
        log.debug("[CacheManager] Invalidated: %s", path)

    def clear_all(self) -> None:
        """Wipe every cache (useful for testing or low-memory situations)."""
        self._text.clear()
        self._chunks.clear()
        self._embeddings.clear()
        log.info("[CacheManager] All caches cleared")

    # ── Observability ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return combined stats for all caches."""
        return {
            "text"      : self._text.stats(),
            "chunks"    : self._chunks.stats(),
            "embeddings": self._embeddings.stats(),
        }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

cache_manager = CacheManager()
