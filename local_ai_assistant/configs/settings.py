"""
configs/settings.py
===================
Centralised, typed configuration for the entire assistant.

Loading order (highest priority first):
  1. Environment variables
  2. .env file at project root
  3. Hard-coded defaults below

Usage::

    from configs.settings import settings

    print(settings.model_name)
    print(settings.docs_path)
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Project root — always the directory that contains this configs/ package
# ---------------------------------------------------------------------------
_CONFIGS_DIR = Path(__file__).parent
PROJECT_ROOT: Path = _CONFIGS_DIR.parent


def _load_dotenv() -> None:
    """Load .env file if it exists (no external dependency required)."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    with env_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Only set if not already overridden by the real environment
            os.environ.setdefault(key, value)


_load_dotenv()


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Immutable application-wide settings.

    All values can be overridden via environment variables or .env.
    """

    # ── LLM ─────────────────────────────────────────────────────────────────
    model_name: str = field(
        default_factory=lambda: _env("MODEL_NAME", "llama3.2:1b")
    )
    model_temperature: float = field(
        default_factory=lambda: _env_float("MODEL_TEMPERATURE", 0.7)
    )
    model_max_tokens: int = field(
        default_factory=lambda: _env_int("MODEL_MAX_TOKENS", 250)
    )

    # ── Embeddings ───────────────────────────────────────────────────────────
    embedding_model: str = field(
        default_factory=lambda: _env(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    embedding_device: str = field(
        default_factory=lambda: _env("EMBEDDING_DEVICE", "cpu")
    )

    # ── Vector store ─────────────────────────────────────────────────────────
    vector_store_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env(
            "VECTOR_STORE_PATH", "data/vector_store_v2"
        )
    )
    vector_store_k: int = field(
        default_factory=lambda: _env_int("VECTOR_STORE_K", 10)
    )
    retrieval_threshold: float = field(
        default_factory=lambda: _env_float("RETRIEVAL_THRESHOLD", 1.5)
    )
    chunk_size: int = field(
        default_factory=lambda: _env_int("CHUNK_SIZE", 1500)
    )
    chunk_overlap: int = field(
        default_factory=lambda: _env_int("CHUNK_OVERLAP", 150)
    )

    # ── Documents ────────────────────────────────────────────────────────────
    docs_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env("DOCS_PATH", "data/documents")
    )

    # ── Reminders ────────────────────────────────────────────────────────────
    reminders_file: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env(
            "REMINDERS_FILE", "data/reminders.json"
        )
    )
    reminder_poll_interval: int = field(
        default_factory=lambda: _env_int("REMINDER_POLL_INTERVAL", 5)
    )
    reminder_fire_window: int = field(
        default_factory=lambda: _env_int("REMINDER_FIRE_WINDOW", 60)
    )

    # ── Email ────────────────────────────────────────────────────────────────
    email_file: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env("EMAIL_FILE", "data/emails.json")
    )
    email_cache_file: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env(
            "EMAIL_CACHE_FILE", "data/email_cache.json"
        )
    )
    email_fetch_cooldown: int = field(
        default_factory=lambda: _env_int("EMAIL_FETCH_COOLDOWN", 30)
    )
    email_fetch_count: int = field(
        default_factory=lambda: _env_int("EMAIL_FETCH_COUNT", 200)
    )

    # Email semantic search settings
    email_vector_store_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env(
            "EMAIL_VECTOR_STORE_PATH", "data/vector_store_email"
        )
    )
    email_embedding_model: str = field(
        default_factory=lambda: _env(
            "EMAIL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    email_semantic_k: int = field(
        default_factory=lambda: _env_int("EMAIL_SEMANTIC_K", 10)
    )
    email_semantic_threshold: float = field(
        default_factory=lambda: _env_float("EMAIL_SEMANTIC_THRESHOLD", 0.4)
    )
    email_sync_interval_hours: int = field(
        default_factory=lambda: _env_int("EMAIL_SYNC_INTERVAL_HOURS", 6)
    )
    email_hybrid_semantic_weight: float = field(
        default_factory=lambda: _env_float("EMAIL_HYBRID_SEMANTIC_WEIGHT", 0.7)
    )

    # Email sending (SMTP) settings — used by email.reply and email.send tools
    # Configure your email provider's SMTP settings:
    # - Gmail: host=smtp.gmail.com, port=587, use_tls=true (requires app password)
    # - Outlook: host=smtp.live.com, port=587, use_tls=true
    # - Custom: set your provider's SMTP server details
    email_host: str = field(
        default_factory=lambda: _env("EMAIL_HOST", "")
    )
    email_port: int = field(
        default_factory=lambda: _env_int("EMAIL_PORT", 587)
    )
    email_user: str = field(
        default_factory=lambda: _env("EMAIL_USER", "")
    )
    email_password: str = field(
        default_factory=lambda: _env("EMAIL_PASS", "")
    )
    email_from: str = field(
        default_factory=lambda: _env("EMAIL_FROM", "")
    )
    email_tls_enabled: bool = field(
        default_factory=lambda: _env_bool("EMAIL_TLS", True)
    )
    audio_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env("AUDIO_PATH", "data/audio")
    )
    audio_vector_store_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env(
            "AUDIO_VECTOR_STORE_PATH", "data/vector_store_audio"
        )
    )

    # ── Windows Documents indexer ─────────────────────────────────────────────
    # Only C:\AI_Test_Documents is the authorised indexed folder.
    # C:\Users\Sandeep\OneDrive\Documents is intentionally NOT indexed.
    windows_docs_path: Path = field(
        default_factory=lambda: Path(_env(
            "WINDOWS_DOCS_PATH",
            r"C:\AI_Test_Documents",
        ))
    )
    windows_docs_vector_store_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env(
            "WINDOWS_DOCS_VECTOR_STORE_PATH", "data/vector_store_win_docs"
        )
    )
    # Comma-separated subfolder names relative to windows_docs_path to restrict
    # the scan.  Empty string (default) = scan the entire Documents folder.
    # Example in .env:  WINDOWS_DOCS_SUBFOLDERS=Work,Projects/AI
    windows_docs_subfolders: tuple = field(
        default_factory=lambda: tuple(
            s.strip()
            for s in _env("WINDOWS_DOCS_SUBFOLDERS", "").split(",")
            if s.strip()
        )
    )

    # ── OCR ──────────────────────────────────────────────────────────────────
    tesseract_cmd: str = field(
        default_factory=lambda: _env(
            "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        )
    )

    # ── Logging ──────────────────────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: _env("LOG_LEVEL", "INFO")
    )
    log_file: str = field(
        default_factory=lambda: _env("LOG_FILE", "")   # empty = console only
    )
    log_format: str = field(
        default_factory=lambda: _env("LOG_FORMAT", "text")  # "text" | "json"
    )

    # ── User identity ────────────────────────────────────────────────────────
    # Used as the reply signature in generated email drafts.
    # Override via USER_NAME env var or .env:  USER_NAME=Alice
    user_name: str = field(
        default_factory=lambda: _env("USER_NAME", "Sandeep")
    )

    # ── MCP ──────────────────────────────────────────────────────────────────
    mcp_transport: str = field(
        default_factory=lambda: _env("MCP_TRANSPORT", "stdio")
    )
    mcp_sse_port: int = field(
        default_factory=lambda: _env_int("MCP_SSE_PORT", 8765)
    )

    # ── Retry ────────────────────────────────────────────────────────────────
    llm_retry_attempts: int = field(
        default_factory=lambda: _env_int("LLM_RETRY_ATTEMPTS", 3)
    )
    llm_retry_delay: float = field(
        default_factory=lambda: _env_float("LLM_RETRY_DELAY", 1.0)
    )

    # ── Derived (for convenience) ─────────────────────────────────────────────
    @property
    def project_root(self) -> Path:
        """Absolute path to the project root directory."""
        return PROJECT_ROOT

    @property
    def logs_path(self) -> Path:
        """Absolute path to the logs directory."""
        return PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Singleton instance — import this everywhere
# ---------------------------------------------------------------------------
settings = Settings()
