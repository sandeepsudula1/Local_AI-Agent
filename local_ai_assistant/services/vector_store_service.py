"""
services/vector_store_service.py
=================================
Async-friendly vector-store service with lazy background loading.

Responsibilities
----------------
- Build or reload the Chroma vector store from local documents.
- Detect stale stores (new document newer than chroma.sqlite3) and rebuild.
- Expose a thread-safe ``get_vector_db()`` getter consumed by retrieval agents
  and ``register_retrieval_tool`` in ``core/tool_registry.py``.
- Register the ``documents.search`` tool once the store is ready.

Usage::

    from services.vector_store_service import vector_store_service

    # Called once at startup — returns immediately; builds in background.
    vector_store_service.start()

    # Later: check readiness
    if vector_store_service.is_ready:
        db = vector_store_service.get_vector_db()
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger
from configs.settings import settings

log = get_logger(__name__)


class VectorStoreService:
    """Manages the ChromaDB vector store lifecycle."""

    def __init__(self) -> None:
        self._db = None           # Chroma instance
        self._ready = False
        self._loading = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    # ── public API ─────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_loading(self) -> bool:
        return self._loading

    def get_vector_db(self):
        """Return the Chroma instance, or ``None`` if not yet ready."""
        with self._lock:
            return self._db

    def start(self, documents: list | None = None, reload: bool = False) -> None:
        """Start background loading in a daemon thread.

        Parameters
        ----------
        documents:
            Pre-loaded ``Document`` list to index.  When omitted the service
            loads documents from ``settings.docs_path`` itself.
        reload:
            Force a full rebuild even if a persisted store already exists.
        """
        if self._loading:
            log.debug("VectorStoreService.start() called but already loading")
            return
        self._thread = threading.Thread(
            target=self._load,
            args=(documents, reload),
            daemon=True,
            name="vector-store-loader",
        )
        self._thread.start()

    def wait(self, timeout: float = 120.0) -> bool:
        """Block until ready or *timeout* seconds elapse.

        Returns ``True`` when ready.
        """
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._ready

    # ── internals ──────────────────────────────────────────────────────────

    def _docs_stale(self) -> bool:
        """Return True when any document is newer than the stored chroma.sqlite3."""
        db_file = settings.vector_store_path / "chroma.sqlite3"
        if not db_file.exists():
            return True
        store_mtime = db_file.stat().st_mtime
        docs_dir = settings.docs_path
        if not docs_dir.exists():
            return False
        for fpath in docs_dir.iterdir():
            if fpath.is_file() and fpath.stat().st_mtime > store_mtime:
                log.info("Document newer than vector store: %s", fpath.name)
                return True
        return False

    def _load(self, documents: list | None, reload: bool) -> None:
        self._loading = True
        try:
            # Import heavy dependencies lazily so startup is fast
            from langchain_community.embeddings import HuggingFaceEmbeddings
            from langchain_community.vectorstores import Chroma
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            emb = HuggingFaceEmbeddings(
                model_name=settings.embedding_model,
                model_kwargs={"device": settings.embedding_device},
            )

            store_path = str(settings.vector_store_path)

            # Rebuild if forced or documents changed
            if reload or self._docs_stale():
                if settings.vector_store_path.exists():
                    log.info("Rebuilding vector store (stale/forced)…")
                    try:
                        shutil.rmtree(store_path)
                    except Exception as exc:
                        log.warning("Could not remove old vector store: %s", exc)

            # Try loading existing persisted store
            if settings.vector_store_path.exists() and any(
                settings.vector_store_path.iterdir()
            ):
                try:
                    log.info("Loading persisted vector store from %s", store_path)
                    db = Chroma(
                        persist_directory=store_path,
                        embedding_function=emb,
                    )
                    with self._lock:
                        self._db = db
                    self._ready = True
                    log.info("Vector store loaded from disk (ready)")
                    self._post_ready()
                    return
                except Exception as exc:
                    log.warning("Failed to load persisted store: %s. Rebuilding…", exc)
                    try:
                        shutil.rmtree(store_path)
                    except Exception:
                        pass

            # Build from documents
            if documents is None:
                documents = self._load_documents()

            if not documents:
                log.warning("No documents found; vector store will be empty")

            log.info("Building vector store from %d document(s)…", len(documents))
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            )
            chunks = splitter.split_documents(documents)

            for attempt in range(2):
                try:
                    settings.vector_store_path.mkdir(parents=True, exist_ok=True)
                    db = Chroma.from_documents(
                        chunks, emb, persist_directory=store_path
                    )
                    try:
                        db.persist()
                    except Exception:
                        pass
                    with self._lock:
                        self._db = db
                    self._ready = True
                    log.info("[Ready] Knowledge base built (%d chunks)", len(chunks))
                    self._post_ready()
                    return
                except sqlite3.OperationalError as exc:
                    if attempt == 0:
                        log.warning(
                            "SQLite error on attempt %d: %s. Retrying after reset…",
                            attempt + 1, exc,
                        )
                        try:
                            shutil.rmtree(store_path)
                            settings.vector_store_path.mkdir(parents=True, exist_ok=True)
                        except Exception:
                            pass
                    else:
                        log.error("Vector store build failed after retry: %s", exc)
                        return
                except Exception as exc:
                    log.error("Vector store build error: %s", exc)
                    return

        finally:
            self._loading = False

    def _load_documents(self) -> list:
        """Load all supported documents from settings.docs_path."""
        from services.document_service import document_service
        return document_service.load_all()

    def _post_ready(self) -> None:
        """Actions to run once the project store is ready."""
        # Notify the coordinator of the project store — documents.search is
        # (re-)registered immediately so queries work right away.
        try:
            from core.tool_registry import update_retrieval_stores
            db = self.get_vector_db()
            update_retrieval_stores(project_db=db)
            log.info("Project vector store notified to coordinator")
        except Exception as exc:
            log.warning("Could not notify coordinator of project store: %s", exc)

        # Start Windows Documents indexer in the background; it will
        # re-register documents.search once its own store is ready.
        try:
            from services.document_indexer_service import document_indexer_service
            log.info(
                "Triggering Windows docs indexer (target: %s)",
                settings.windows_docs_path,
            )
            document_indexer_service.start()
            log.info("Windows docs indexer started in background")
        except Exception as exc:
            log.warning("Could not start Windows docs indexer: %s", exc)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
vector_store_service = VectorStoreService()
