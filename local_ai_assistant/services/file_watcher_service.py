"""
services/file_watcher_service.py
=================================
Real-time file-system watcher...
(unchanged docstring)
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger
from configs.settings import PROJECT_ROOT, DATA_DIR   # ✅ ADDED

log = get_logger(__name__)

POLL_INTERVAL: float = 30.0

_WATCHED_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".pdf", ".docx", ".csv", ".json",
    ".py", ".js", ".ts", ".java", ".pptx", ".xlsx",
    ".png", ".jpg", ".jpeg",
})


# ✅ UPDATED FUNCTION ONLY
def _default_watch_paths() -> list[Path]:
    """Return the default set of directories to watch."""

    home = Path.home()

    user_paths = [
        home / "Documents",
    ]

    # ✅ NEW: project-controlled folder (important for portability)
    project_documents = Path(DATA_DIR) / "documents"
    project_documents.mkdir(parents=True, exist_ok=True)

    candidates = user_paths + [project_documents]

    return [p for p in candidates if p.is_dir()]


# ------------------ REMAINING CODE (UNCHANGED) ------------------

class _IndexEventHandler:
    def __init__(self):
        self._last_event = {}
        self._lock = threading.Lock()

    def dispatch(self, event) -> None:
        try:
            src = getattr(event, "src_path", None)
            dst = getattr(event, "dest_path", None)
            ev_type = getattr(event, "event_type", "")
            is_dir  = getattr(event, "is_directory", False)

            if is_dir or not src:
                return

            path_obj = Path(src)
            ext = path_obj.suffix.lower()
            if ext not in _WATCHED_EXTENSIONS:
                return
                
            name = path_obj.name
            if name.startswith("~") or name.startswith(".") or name.endswith(".tmp") or name.endswith(".part") or name.endswith(".temp"):
                return
                
            with self._lock:
                now = time.time()
                if now - self._last_event.get(src, 0) < 3.0:
                    return
                self._last_event[src] = now

            from services.file_indexer_service import file_indexer
            from memory.conversation_memory import conversation_memory

            if ev_type == "created":
                log.info("[FileWatcher] CREATE: %s", src)
                file_indexer.register_file(src)
                conversation_memory.register_file(Path(src).name, src)

            elif ev_type == "modified":
                log.info("[FileWatcher] MODIFY: %s", src)
                file_indexer.register_file(src)
                file_indexer.mark_stale(src)
                conversation_memory.register_file(Path(src).name, src)

            elif ev_type in ("deleted", "moved"):
                log.info("[FileWatcher] DELETE/MOVE: %s", src)
                file_indexer.remove(src)
                if ev_type == "moved" and dst:
                    file_indexer.register_file(dst)
                    conversation_memory.register_file(Path(dst).name, dst)

        except Exception as exc:
            log.debug("[FileWatcher] handler error: %s", exc)


class FileWatcherService:

    def __init__(self) -> None:
        self._watch_paths: set[Path] = set(_default_watch_paths())
        self._observer = None
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._snapshot: dict[str, float] = {}

    def watch(self, folder: str) -> None:
        p = Path(folder).resolve()
        if not p.is_dir():
            log.warning("[FileWatcher] watch(%r): not a directory — skipped", folder)
            return

        with self._lock:
            if p in self._watch_paths:
                return
            self._watch_paths.add(p)
            log.info("[FileWatcher] Added watch path: %s", p)

        if self._observer is not None:
            try:
                handler = _IndexEventHandler()
                self._observer.schedule(handler, str(p), recursive=False)
            except Exception as exc:
                log.debug("[FileWatcher] Could not add live watch for %s: %s", p, exc)

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class _WDHandler(FileSystemEventHandler):
                def __init__(self_inner):
                    self_inner._delegate = _IndexEventHandler()

                def dispatch(self_inner, event):
                    self_inner._delegate.dispatch(event)

            obs = Observer()
            handler = _WDHandler()

            with self._lock:
                paths_snapshot = list(self._watch_paths)

            for p in paths_snapshot:
                if p.is_dir():
                    obs.schedule(handler, str(p), recursive=False)
                    log.info("[FileWatcher] watchdog watching: %s", p)

            obs.start()
            self._observer = obs
            log.info("[FileWatcher] watchdog Observer started")

        except ImportError:
            log.info("[FileWatcher] watchdog not installed — using polling scanner")

        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="file-watcher-poll",
        )
        self._poll_thread.start()

        threading.Thread(
            target=self._full_scan,
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

    def _poll_loop(self) -> None:
        while self._running:
            self._full_scan()
            time.sleep(POLL_INTERVAL)

    def _full_scan(self) -> None:
        from services.file_indexer_service import file_indexer
        from memory.conversation_memory import conversation_memory

        with self._lock:
            paths = list(self._watch_paths)

        for folder in paths:
            if not folder.is_dir():
                continue

            for entry in folder.iterdir():
                if not entry.is_file():
                    continue

                if entry.suffix.lower() not in _WATCHED_EXTENSIONS:
                    continue
                    
                name = entry.name
                if name.startswith("~") or name.startswith(".") or name.endswith(".tmp") or name.endswith(".part") or name.endswith(".temp"):
                    continue

                path_str = str(entry)
                mtime = entry.stat().st_mtime

                prev = self._snapshot.get(path_str)

                if prev is None:
                    file_indexer.register_file(path_str)
                    conversation_memory.register_file(entry.name, path_str)
                    self._snapshot[path_str] = mtime

                elif abs(prev - mtime) > 0.5:
                    file_indexer.register_file(path_str)
                    file_indexer.mark_stale(path_str)
                    conversation_memory.register_file(entry.name, path_str)
                    self._snapshot[path_str] = mtime


# Singleton
file_watcher = FileWatcherService()