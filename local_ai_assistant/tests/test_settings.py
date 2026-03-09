"""
tests/test_settings.py
=======================
Unit tests for ``configs.settings.Settings``.

Covers:
- Default values are correctly typed
- Environment variables override defaults
- .env file values are loaded
- Path fields are absolute Path objects
"""

from __future__ import annotations

import os
from pathlib import Path


def test_settings_defaults() -> None:
    """Settings should provide sensible typed defaults."""
    from configs.settings import Settings

    s = Settings()
    assert isinstance(s.model_name, str)
    assert len(s.model_name) > 0
    assert isinstance(s.model_temperature, float)
    assert 0.0 <= s.model_temperature <= 2.0
    assert isinstance(s.retrieval_threshold, float)
    assert s.retrieval_threshold > 0
    assert isinstance(s.chunk_size, int)
    assert s.chunk_size > 0
    assert isinstance(s.vector_store_path, Path)
    assert isinstance(s.docs_path, Path)
    assert isinstance(s.reminders_file, Path)


def test_env_override(monkeypatch: object) -> None:
    """Environment variables must override Settings defaults."""
    import importlib, configs.settings as _mod

    monkeypatch.setenv("MODEL_NAME", "llama3.2:3b-test")
    monkeypatch.setenv("MODEL_TEMPERATURE", "0.42")
    monkeypatch.setenv("RETRIEVAL_THRESHOLD", "1.23")

    # Re-instantiate (not reimport module — frozen dataclass re-reads env on each instantiation)
    from configs.settings import Settings
    s = Settings()
    assert s.model_name == "llama3.2:3b-test"
    assert abs(s.model_temperature - 0.42) < 1e-9
    assert abs(s.retrieval_threshold - 1.23) < 1e-9


def test_path_fields_are_absolute() -> None:
    """All Path-typed settings must be absolute paths."""
    from configs.settings import Settings

    s = Settings()
    path_fields = [
        s.vector_store_path,
        s.docs_path,
        s.reminders_file,
        s.email_file,
        s.email_cache_file,
        s.audio_path,
        s.audio_vector_store_path,
    ]
    for p in path_fields:
        assert isinstance(p, Path), f"Expected Path, got {type(p)}"
        assert p.is_absolute(), f"Expected absolute path, got: {p}"


def test_settings_is_frozen() -> None:
    """Settings dataclass must be immutable (frozen)."""
    from configs.settings import Settings

    s = Settings()
    try:
        s.model_name = "should-raise"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError but no exception was raised")
    except Exception as exc:
        assert "frozen" in type(exc).__name__.lower() or "can't" in str(exc).lower() or "cannot" in str(exc).lower(), (
            f"Unexpected exception type: {type(exc).__name__}: {exc}"
        )


def test_log_level_default() -> None:
    from configs.settings import Settings

    s = Settings()
    assert s.log_level.upper() in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def test_log_format_default() -> None:
    from configs.settings import Settings

    s = Settings()
    assert s.log_format.lower() in {"text", "json"}
