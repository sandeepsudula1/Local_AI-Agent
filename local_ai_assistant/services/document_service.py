"""
services/document_service.py
=============================
Document ingestion service — loads all supported file types from
``settings.docs_path`` into LangChain ``Document`` objects.

Supported formats
-----------------
- PDF  → PyPDFLoader
- CSV  → row-per-record with labeled columns
- PNG / JPG / JPEG → OCR via pytesseract

Usage::

    from services.document_service import document_service

    docs = document_service.load_all()          # fresh load
    docs = document_service.get_documents()     # cached load (loads once)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger
from configs.settings import settings

log = get_logger(__name__)


class DocumentService:
    """Loads and caches documents from the configured docs directory."""

    def __init__(self) -> None:
        self._documents: Optional[list] = None

    # ── public API ─────────────────────────────────────────────────────────

    def get_documents(self) -> list:
        """Return cached documents, loading them on first call."""
        if self._documents is None:
            self._documents = self.load_all()
        return self._documents

    def invalidate(self) -> None:
        """Clear cached documents so the next call reloads from disk."""
        self._documents = None

    def load_all(self) -> list:
        """Load all documents from ``settings.docs_path``. No caching."""
        docs_path: Path = settings.docs_path
        if not docs_path.exists():
            log.warning("Docs path does not exist: %s", docs_path)
            return []

        all_docs: list = []
        for fpath in sorted(docs_path.iterdir()):
            if not fpath.is_file():
                continue
            loaded = self._load_file(fpath)
            all_docs.extend(loaded)

        log.info("Loaded %d document chunk(s) from %s", len(all_docs), docs_path)
        return all_docs

    # ── per-file loaders ───────────────────────────────────────────────────

    def _load_file(self, fpath: Path) -> list:
        suffix = fpath.suffix.lower()
        try:
            if suffix == ".pdf":
                return self._load_pdf(fpath)
            if suffix == ".csv":
                return self._load_csv(fpath)
            if suffix in {".png", ".jpg", ".jpeg"}:
                return self._load_image(fpath)
        except Exception as exc:
            log.warning("Could not load %s: %s", fpath.name, exc)
        return []

    def _load_pdf(self, fpath: Path) -> list:
        from langchain_community.document_loaders import PyPDFLoader
        loader = PyPDFLoader(str(fpath))
        docs = []
        for doc in loader.load():
            doc.metadata["source"] = fpath.name
            docs.append(doc)
        log.debug("PDF loaded: %s (%d pages)", fpath.name, len(docs))
        return docs

    def _load_csv(self, fpath: Path) -> list:
        import pandas as pd
        from langchain_core.documents import Document

        df = pd.read_csv(fpath)
        docs = []
        for _, row in df.iterrows():
            row_text = ", ".join(
                f"{col}: {row[col]}" for col in df.columns
            )
            docs.append(
                Document(page_content=row_text, metadata={"source": fpath.name})
            )
        log.debug("CSV loaded: %s (%d rows)", fpath.name, len(docs))
        return docs

    def _load_image(self, fpath: Path) -> list:
        import pytesseract
        from PIL import Image
        from langchain_core.documents import Document

        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        try:
            extracted = pytesseract.image_to_string(Image.open(fpath)).strip()
        except Exception as exc:
            log.warning("OCR failed for %s: %s", fpath.name, exc)
            return []

        if extracted:
            log.debug("Image OCR loaded: %s", fpath.name)
            return [Document(page_content=extracted, metadata={"source": fpath.name})]
        else:
            log.info("No OCR text extracted from %s; skipped", fpath.name)
            return []


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
document_service = DocumentService()
