"""
core/logging_config.py
======================
Structured, centralised logging for the entire assistant.

Features
--------
- Single call to ``setup_logging()`` configures the root logger once.
- Supports plain-text (human readable) and JSON (machine readable) formats.
- Optional file handler (rotated daily, 7 files kept).
- Suppresses noisy third-party library chatter (transformers, chromadb, etc.).
- ``get_logger(__name__)`` returns a named child logger with the right level.

Usage::

    from core.logging_config import get_logger

    log = get_logger(__name__)
    log.info("Vector store loaded", extra={"doc_count": 42})
    log.warning("Retrieval threshold exceeded", extra={"score": 2.1})
    log.error("LLM call failed", exc_info=True)
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SETUP_DONE = False  # guard against double-initialisation


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line — ideal for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = traceback.format_exception(*record.exc_info)
        # Merge any extra= kwargs that aren't standard LogRecord fields
        _STANDARD = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread",
            "threadName",
        }
        for k, v in record.__dict__.items():
            if k not in _STANDARD and not k.startswith("_"):
                payload[k] = v
        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Plain-text formatter
# ---------------------------------------------------------------------------

_TEXT_FMT = "%(asctime)s [%(levelname)-8s] %(name)-30s %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Noisy libraries to silence
# ---------------------------------------------------------------------------

_QUIET_LOGGERS = [
    "transformers",
    "sentence_transformers",
    "hf_xai_core",
    "mlx",
    "chromadb",
    "httpx",
    "urllib3",
    "langchain",
    "langchain_community",
    "langchain_core",
    "openai",
    "PIL",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = "INFO",
    log_format: str = "text",
    log_file: str = "",
) -> None:
    """Configure the root logger.  Call once at application startup.

    Parameters
    ----------
    level:
        One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    log_format:
        ``"text"`` for human-readable output, ``"json"`` for structured logs.
    log_file:
        Optional path to a log file.  Empty string = console only.
    """
    global _SETUP_DONE
    if _SETUP_DONE:
        return
    _SETUP_DONE = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter: logging.Formatter
    if log_format.lower() == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(_TEXT_FMT, datefmt=_DATE_FMT)

    # ── console handler ─────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # ── file handler (optional, daily rotation) ─────────────────────────────
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_path),
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # ── silence noisy libraries ──────────────────────────────────────────────
    for lib in _QUIET_LOGGERS:
        logging.getLogger(lib).setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    Call ``setup_logging()`` first (or rely on the lazy call inside
    ``_ensure_setup()``).

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module.
    """
    _ensure_setup()
    return logging.getLogger(name)


def _ensure_setup() -> None:
    """Lazy bootstrap with defaults so libraries can log without main setup."""
    if not _SETUP_DONE:
        setup_logging()
