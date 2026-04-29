"""
engines/embedding_engine.py
============================
Shared embedding service for documents and emails.

Uses Sentence Transformers to convert text into dense vector embeddings.
Abstracts the embedding model so you can swap implementations easily.

Usage::

    from engines.embedding_engine import embedding_engine

    # Single text
    embedding = embedding_engine.embed("What is machine learning?")

    # Batch (faster for many items)
    embeddings = embedding_engine.embed_batch([text1, text2, text3])
"""

from __future__ import annotations

import os
from typing import Optional
from functools import lru_cache

from core.logging_config import get_logger

log = get_logger(__name__)


class EmbeddingEngine:
    """Wrapper around Sentence Transformers for text embeddings."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        """
        Initialize embedding engine.

        Parameters
        ----------
        model_name:
            HuggingFace model ID. Default is lightweight and fast.
            Alternatives:
            - "all-mpnet-base-v2": Better quality, slower
            - "paraphrase-distilroberta-base-v1": For paraphrases
            - "multi-qa-MiniLM-L6-cos-v1": Optimized for QA
        """
        self.model_name = model_name
        self._model = None
        self._ready = False
        self._error = None

    def load(self) -> bool:
        """Load the embedding model. Returns True on success."""
        if self._model is not None:
            return True

        # ── Pre-checks for critical dependencies ──────────────────────────────
        # These often fail silently in PyInstaller, so we check explicitly.
        # Skip this check in frozen mode - circular imports in PyInstaller bundles
        # are resolved at import time, not when calling __import__()
        import sys
        is_frozen = getattr(sys, 'frozen', False)
        
        if not is_frozen:
            critical_deps = ['packaging', 'transformers', 'tokenizers', 'safetensors']
            for dep in critical_deps:
                try:
                    __import__(dep)
                except (ImportError, AttributeError) as e:
                    # AttributeError can occur from circular imports in frozen bundles
                    self._error = (
                        f"Missing critical dependency '{dep}' required for embeddings. "
                        f"This usually indicates a PyInstaller packaging issue. "
                        f"Original error: {e}"
                    )
                    log.error(self._error)
                    return False

        try:
            from sentence_transformers import SentenceTransformer
            log.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
            self._ready = True
            log.info("Embedding model loaded successfully")
            return True
        except ModuleNotFoundError as e:
            # Specific handling for missing module errors
            if 'packaging' in str(e):
                self._error = (
                    f"Missing 'packaging' module when loading embeddings. "
                    f"This is a PyInstaller bundling issue. "
                    f"The fixed .spec file should include 'packaging' in hiddenimports. "
                    f"Original error: {e}"
                )
            else:
                self._error = f"Missing module when loading embeddings: {e}"
            log.error(self._error)
            return False
        except AttributeError as e:
            # Circular import from torch in frozen bundles
            if 'autograd' in str(e) or 'partially initialized module' in str(e):
                self._error = (
                    f"Circular import issue with torch in frozen bundle. "
                    f"This is expected in PyInstaller - embeddings will not be available. "
                    f"Original error: {e}"
                )
                log.warning(self._error)
                # Don't fail - let the system continue without embeddings
                self._model = None
                return False
            else:
                self._error = f"Attribute error loading embeddings: {e}"
                log.error(self._error)
                return False
        except ImportError as e:
            self._error = f"Import error loading embeddings: {e}"
            log.error(self._error)
            return False
        except Exception as e:
            self._error = str(e)
            log.error("Failed to load embedding model: %s", e)
            return False

    @property
    def is_ready(self) -> bool:
        """Check if model is loaded and ready."""
        return self._ready and self._model is not None
    
    @property
    def error_message(self) -> Optional[str]:
        """Get the error message if loading failed."""
        return self._error

    @property
    def embedding_dim(self) -> Optional[int]:
        """Return embedding dimension (e.g., 384 for MiniLM)."""
        if not self.is_ready:
            return None
        return self._model.get_sentence_embedding_dimension()

    def embed(self, text: str, normalize: bool = False) -> Optional[list]:
        """
        Convert a single text string into an embedding vector.

        Parameters
        ----------
        text:
            Input text to embed.
        normalize:
            If True, normalize the embedding to unit length (for cosine similarity).

        Returns
        -------
        Embedding as a list of floats, or None if failed.
        """
        if not self.is_ready:
            log.warning("Embedding engine not ready")
            return None

        try:
            if not text or not isinstance(text, str):
                return None

            # Truncate very long texts (Transformers have max token limits)
            text = text[:512]  # ~128 tokens for most tokenizers

            embeddings = self._model.encode(text, convert_to_numpy=False)
            embedding_list = embeddings.tolist() if hasattr(embeddings, 'tolist') else list(embeddings)

            if normalize:
                # L2 normalization for cosine similarity
                import math
                norm = math.sqrt(sum(x**2 for x in embedding_list))
                if norm > 0:
                    embedding_list = [x / norm for x in embedding_list]

            return embedding_list
        except Exception as e:
            log.error("Embedding failed for text (first 100 chars): %s | Error: %s", text[:100], e)
            return None

    def embed_batch(self, texts: list[str], normalize: bool = False, batch_size: int = 32) -> list:
        """
        Embed multiple texts efficiently in batches.

        Parameters
        ----------
        texts:
            List of strings to embed.
        normalize:
            If True, L2-normalize each embedding.
        batch_size:
            Process N texts at once (higher = faster but more memory).

        Returns
        -------
        List of embedding vectors (same length as input texts).
        """
        if not self.is_ready:
            log.warning("Embedding engine not ready")
            return []

        if not texts:
            return []

        try:
            # Truncate all texts
            texts = [t[:512] if isinstance(t, str) else "" for t in texts]

            # Batch encode
            embeddings = self._model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=False,
                show_progress_bar=False
            )

            # Convert to list of lists
            result = []
            for emb in embeddings:
                emb_list = emb.tolist() if hasattr(emb, 'tolist') else list(emb)
                if normalize:
                    import math
                    norm = math.sqrt(sum(x**2 for x in emb_list))
                    if norm > 0:
                        emb_list = [x / norm for x in emb_list]
                result.append(emb_list)

            log.info("Embedded %d texts successfully", len(texts))
            return result
        except Exception as e:
            log.error("Batch embedding failed: %s", e)
            return []

    def max_tokens(self) -> int:
        """Return max token length the model supports."""
        # Most Transformers: ~512 tokens (4096 chars)
        return 256  # Conservative estimate: 256 tokens ≈ 2000 chars


# Singleton instance
_embedding_engine: Optional[EmbeddingEngine] = None


def get_embedding_engine(model_name: str = "all-MiniLM-L6-v2") -> EmbeddingEngine:
    """Get or create the singleton embedding engine."""
    global _embedding_engine
    if _embedding_engine is None:
        _embedding_engine = EmbeddingEngine(model_name)
    return _embedding_engine
