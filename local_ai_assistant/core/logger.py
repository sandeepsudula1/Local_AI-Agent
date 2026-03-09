"""
core/logger.py
==============
Structured request/response logger — every agent turn is written as a
JSON line to ``logs/agent.log`` so the system is observable and auditable.

Logged fields
-------------
- ``ts``          — ISO-8601 timestamp (UTC)
- ``query``       — raw user input
- ``intent``      — detected intent label
- ``tool``        — tool name invoked (may be null)
- ``result``      — first 200 chars of the answer (truncated for log size)
- ``source``      — source document file, if any
- ``latency_ms``  — total wall-clock time for this request
- ``error``       — exception message, if any

Usage::

    from core.logger import agent_logger

    agent_logger.log_request(
        query="how many employees in 2024",
        intent="RETRIEVAL",
        tool="documents.search",
        result="There are 150 employees in 2024.",
        latency_ms=312.5,
        source="company_data.csv",
    )

    agent_logger.log_error(
        query="transcribe audio.mp3",
        error="FileNotFoundError: audio.mp3 not found",
        intent="AUDIO_TRANSCRIBE",
    )
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOG_FILE_NAME = "agent.log"


def _resolve_log_dir() -> Path:
    try:
        from configs.settings import settings
        return settings.logs_path
    except Exception:
        return Path(__file__).parent.parent / "logs"


class _RequestFormatter(logging.Formatter):
    """Emits structured JSON lines for agent request/response events."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {k: v for k, v in record.__dict__.items()
                   if not k.startswith("_") and k not in {
                       "name", "msg", "args", "levelname", "levelno",
                       "pathname", "filename", "module", "exc_info",
                       "exc_text", "stack_info", "lineno", "funcName",
                       "created", "msecs", "relativeCreated", "thread",
                       "threadName", "processName", "process",
                       "message", "asctime", "taskName",
                   }}
        payload["msg"] = record.getMessage()
        payload["level"] = record.levelname
        return json.dumps(payload, default=str, ensure_ascii=False)


class AgentLogger:
    """High-level logger for agent request/response events."""

    def __init__(self) -> None:
        self._logger: Optional[logging.Logger] = None

    def _get_logger(self) -> logging.Logger:
        if self._logger is not None:
            return self._logger

        log_dir = _resolve_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / _LOG_FILE_NAME

        logger = logging.getLogger("agent.requests")
        if logger.handlers:
            self._logger = logger
            return logger

        logger.setLevel(logging.INFO)
        logger.propagate = False  # don't duplicate into root logger

        # Rotating file handler  — 5 MB per file, 7 backups
        fh = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        fh.setFormatter(_RequestFormatter())
        logger.addHandler(fh)

        self._logger = logger
        return logger

    # ── public API ─────────────────────────────────────────────────────────

    def log_request(
        self,
        query: str,
        intent: str,
        tool: Optional[str] = None,
        result: Optional[str] = None,
        latency_ms: float = 0.0,
        source: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log a completed agent turn."""
        logger = self._get_logger()
        extra = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query[:300],                          # cap length
            "intent": intent,
            "tool": tool,
            "result": (result or "")[:200],
            "source": source,
            "latency_ms": round(latency_ms, 1),
            "error": error,
        }
        if error:
            logger.error("agent_request", extra=extra)
        else:
            logger.info("agent_request", extra=extra)

    def log_error(
        self,
        query: str,
        error: str,
        intent: Optional[str] = None,
    ) -> None:
        """Log a request that failed with an exception."""
        self.log_request(
            query=query,
            intent=intent or "UNKNOWN",
            error=error,
        )

    def log_tool_call(
        self,
        tool_name: str,
        args: dict,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """Log a low-level tool invocation (separate from request log)."""
        logger = self._get_logger()
        extra = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": {k: str(v)[:100] for k, v in args.items()},
            "success": success,
            "latency_ms": round(latency_ms, 1),
        }
        if success:
            logger.info("tool_call", extra=extra)
        else:
            logger.warning("tool_call_failed", extra=extra)

    @property
    def log_file(self) -> Path:
        return _resolve_log_dir() / _LOG_FILE_NAME


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
agent_logger = AgentLogger()
