"""
services/db_service.py
=======================
SQLite helper for file metadata index (data/file_index.db)

All services use the same database file.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from configs.settings import PROJECT_ROOT, DATA_DIR

# ✅ Ensure PROJECT_ROOT is absolute
PROJECT_ROOT = Path(PROJECT_ROOT).resolve()

# ✅ Portable DB path — use writable DATA_DIR
_DB_PATH: Path = Path(DATA_DIR) / "file_index.db"


def get_connection(db_path: Path = _DB_PATH) -> sqlite3.Connection:
    """Return SQLite connection"""

    # ✅ Ensure data folder exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def insert_file(meta: dict) -> bool:
    """Insert new file"""

    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO files
                   (path, name, extension, size_bytes, mtime, content_hint,
                    chunk_stale, indexed_at)
                   VALUES (:path, :name, :extension, :size_bytes, :mtime,
                           :content_hint, 1, :indexed_at)""",
                {
                    "path":         str(meta.get("path", "")),
                    "name":         str(meta.get("name", "")).lower(),
                    "extension":    str(meta.get("extension", "")),
                    "size_bytes":   int(meta.get("size_bytes", 0)),
                    "mtime":        float(meta.get("mtime", 0.0)),
                    "content_hint": str(meta.get("content_hint", "")),
                    "indexed_at":   time.time(),
                },
            )
        return True
    except Exception:
        return False


def update_file(meta: dict) -> bool:
    """Update file metadata"""

    try:
        path = str(meta.get("path", ""))

        with get_connection() as conn:
            conn.execute(
                """UPDATE files
                   SET name        = COALESCE(:name, name),
                       extension   = COALESCE(:extension, extension),
                       size_bytes  = COALESCE(:size_bytes, size_bytes),
                       mtime       = COALESCE(:mtime, mtime),
                       content_hint= COALESCE(:content_hint, content_hint),
                       chunk_stale = 1,
                       indexed_at  = :indexed_at
                   WHERE path = :path""",
                {
                    "path":         path,
                    "name":         meta.get("name"),
                    "extension":    meta.get("extension"),
                    "size_bytes":   meta.get("size_bytes"),
                    "mtime":        meta.get("mtime"),
                    "content_hint": meta.get("content_hint"),
                    "indexed_at":   time.time(),
                },
            )
        return True
    except Exception:
        return False


def delete_file(path: str) -> bool:
    """Delete file record"""

    try:
        norm = str(Path(path).resolve())

        with get_connection() as conn:
            conn.execute("DELETE FROM files WHERE path = ?", (norm,))

        return True
    except Exception:
        return False


def search_files_by_name(query: str, limit: int = 20) -> list[dict]:
    """Search files by name"""

    try:
        q_text = query.lower().strip()
        if len(q_text) < 3:
            return []
        q = f"%{q_text}%"

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT path, name, extension, size_bytes, mtime "
                "FROM files WHERE name LIKE ? ORDER BY name LIMIT ?",
                (q, limit),
            ).fetchall()

        return [dict(r) for r in rows]
    except Exception:
        return []


def list_files(limit: int = 50, folder_prefix: str | None = None) -> list[dict]:
    """List files, optionally scoped to paths that start with *folder_prefix*."""

    try:
        with get_connection() as conn:
            if folder_prefix:
                # Normalise to a consistent separator and ensure trailing sep so
                # "C:\\AI_Test" cannot accidentally match "C:\\AI_Test_Something".
                import os as _os
                _prefix = _os.path.normcase(
                    folder_prefix.rstrip("/\\") + _os.sep
                )
                rows = conn.execute(
                    "SELECT path, name, extension, size_bytes, mtime "
                    "FROM files WHERE path LIKE ? ORDER BY mtime DESC LIMIT ?",
                    (_prefix.replace("[", "[[").replace("%", "[%]").replace("_", "[_]") + "%", limit),
                ).fetchall()
                # SQLite LIKE is case-insensitive on ASCII; do a precise Python
                # filter to handle mixed-case Windows paths correctly.
                norm = _os.path.normcase
                return [
                    dict(r) for r in rows
                    if norm(dict(r)["path"]).startswith(_prefix)
                ]
            rows = conn.execute(
                "SELECT path, name, extension, size_bytes, mtime "
                "FROM files ORDER BY mtime DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []