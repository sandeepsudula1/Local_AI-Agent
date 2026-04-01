"""
services/email_vector_store_service.py
=======================================
ChromaDB vector store for semantic email search.

Builds and maintains embeddings for emails, enabling semantic similarity search.
Similar architecture to vector_store_service.py but optimized for emails:
- Each email = 1 document (not chunked)
- Metadata includes sender, date, subject
- Incremental updates supported
- Background loading

Usage::

    from services.email_vector_store_service import email_vector_store_service

    # Start loading in background
    email_vector_store_service.start()

    # Check readiness
    if email_vector_store_service.is_ready:
        db = email_vector_store_service.get_vector_db()
        results = db.similarity_search("meeting with john", k=5)
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import hashlib
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

from core.logging_config import get_logger
from configs.settings import settings
from engines.embedding_engine import get_embedding_engine

log = get_logger(__name__)


class EmailVectorStoreService:
    """Manages ChromaDB vector store for email embeddings."""

    def __init__(self) -> None:
        self._db = None
        self._ready = False
        self._loading = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._manifest_path = os.path.join(
            settings.email_vector_store_path, "manifest.json"
        )

    # ── public API ─────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """Whether vector store is loaded and ready."""
        return self._ready

    @property
    def is_loading(self) -> bool:
        """Whether currently loading in background."""
        return self._loading

    def get_vector_db(self):
        """Return ChromaDB instance, or None if not ready."""
        with self._lock:
            return self._db

    def start(self, emails: list | None = None, rebuild: bool = False) -> None:
        """
        Start background loading.

        Parameters
        ----------
        emails:
            Pre-loaded email list to index. If None, loads from cache files.
        rebuild:
            Force full rebuild even if persisted store exists.
        """
        if self._loading:
            log.debug("EmailVectorStoreService already loading")
            return

        self._thread = threading.Thread(
            target=self._load,
            args=(emails, rebuild),
            daemon=True,
            name="email-vector-store-loader",
        )
        self._thread.start()

    def wait(self, timeout: float = 120.0) -> bool:
        """Block until ready or timeout. Returns True when ready."""
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._ready

    # ── internal implementation ────────────────────────────────────────────

    def _load(self, emails: list | None = None, rebuild: bool = False) -> None:
        """Load/rebuild the vector store in background."""
        with self._lock:
            self._loading = True

        try:
            if emails is None:
                emails = self._load_emails()

            if not emails:
                log.warning("No emails to index")
                return

            log.info("Building email vector store from %d emails", len(emails))

            # Initialize ChromaDB
            self._db = self._init_chroma(rebuild)
            if self._db is None:
                return

            # Embed and store emails
            self._index_emails(emails)

            # Save manifest
            self._save_manifest(emails)

            log.info("Email vector store ready")
            self._ready = True

        except Exception as e:
            log.error("Failed to load email vector store: %s", e)
            self._ready = False
        finally:
            self._loading = False

    def _load_emails(self) -> list:
        """Load emails from cache files."""
        emails = []

        # Try email_cache.json first (has live data)
        cache_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "email_cache.json"
        )
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        emails.extend(data.get("emails", []))
                    elif isinstance(data, list):
                        emails.extend(data)
                    log.info("Loaded %d emails from cache", len(emails))
            except Exception as e:
                log.error("Failed to load email cache: %s", e)

        # Fall back to emails.json (static)
        emails_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "emails.json"
        )
        if os.path.exists(emails_file) and not emails:
            try:
                with open(emails_file, "r", encoding="utf-8") as f:
                    emails = json.load(f)
                    log.info("Loaded %d emails from static file", len(emails))
            except Exception as e:
                log.error("Failed to load emails.json: %s", e)

        return emails

    def _init_chroma(self, rebuild: bool = False) -> Any:
        """Initialize ChromaDB collection."""
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            store_path = settings.email_vector_store_path
            os.makedirs(store_path, exist_ok=True)

            # Remove old store if rebuild requested
            if rebuild and os.path.exists(store_path):
                shutil.rmtree(store_path)
                os.makedirs(store_path, exist_ok=True)

            # Initialize client
            client = chromadb.Client(
                ChromaSettings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=store_path,
                    anonymized_telemetry=False,
                )
            )

            # Get or create collection
            collection = client.get_or_create_collection(
                name="emails",
                metadata={"hnsw:space": "cosine"},
            )

            log.info("ChromaDB initialized at %s", store_path)
            return collection
        except Exception as e:
            log.error("Failed to initialize ChromaDB: %s", e)
            return None

    def _index_emails(self, emails: list) -> None:
        """Embed and store emails in ChromaDB."""
        if not self._db or not emails:
            return

        engine = get_embedding_engine(settings.email_embedding_model)
        if not engine.is_ready:
            if not engine.load():
                log.error("Failed to load embedding engine")
                return

        log.info("Embedding %d emails...", len(emails))

        # Prepare email content (subject + body)
        texts = []
        ids = []
        metadatas = []

        for email in emails:
            email_id = str(email.get("id", ""))
            if not email_id:
                email_id = hashlib.md5(
                    str(email).encode()
                ).hexdigest()

            subject = email.get("subject", "")
            body = email.get("body", "")[:1000]  # Truncate body
            sender = email.get("from", "")
            date_str = email.get("date", "")

            # Combine subject + body for embedding
            text = f"{subject}\n{body}"
            texts.append(text)
            ids.append(email_id)

            metadatas.append({
                "sender": sender,
                "subject": subject,
                "date": date_str,
                "id": email_id,
            })

        # Batch embed
        embeddings = engine.embed_batch(texts, batch_size=32)

        if not embeddings or len(embeddings) != len(texts):
            log.error("Embedding failed or size mismatch")
            return

        # Store in ChromaDB in batches (avoid memory issues)
        batch_size = 50
        for i in range(0, len(texts), batch_size):
            batch_end = min(i + batch_size, len(texts))
            log.debug("Adding emails %d-%d to vector store", i, batch_end)

            self._db.add(
                ids=ids[i:batch_end],
                embeddings=embeddings[i:batch_end],
                documents=texts[i:batch_end],
                metadatas=metadatas[i:batch_end],
            )

        log.info("Indexed %d emails successfully", len(emails))

    def _save_manifest(self, emails: list) -> None:
        """Save metadata about what was indexed."""
        os.makedirs(os.path.dirname(self._manifest_path), exist_ok=True)

        manifest = {
            "indexed_at": datetime.now().isoformat(),
            "email_count": len(emails),
            "model": settings.email_embedding_model,
            "store_path": settings.email_vector_store_path,
        }

        try:
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            log.error("Failed to save manifest: %s", e)


# Singleton instance
_email_vector_store_service: Optional[EmailVectorStoreService] = None


def get_email_vector_store_service() -> EmailVectorStoreService:
    """Get or create singleton instance."""
    global _email_vector_store_service
    if _email_vector_store_service is None:
        _email_vector_store_service = EmailVectorStoreService()
    return _email_vector_store_service


# Convenience reference
email_vector_store_service = get_email_vector_store_service()
