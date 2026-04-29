"""
core/permission_store.py
========================
Dynamic folder-permission store for the assistant.

- Maintains the set of *runtime-granted* folders (on top of the static
  ``ALLOWED_FOLDERS`` list in ``access_control.py``).
- Persists granted folders to ``data/granted_folders.json`` so access
  survives between restarts.
- Tracks a single in-flight permission request (pending folder + its
  originating query) so the orchestrator can re-run the query after
  the user approves.

Public API
----------
``permission_store.is_granted(path)``          → bool
``permission_store.grant(path)``               → None
``permission_store.revoke(path)``              → None
``permission_store.get_granted_folders()``     → list[str]
``permission_store.set_pending(folder, query)`` → None
``permission_store.get_pending()``             → (folder, query) | (None, None)
``permission_store.clear_pending()``           → None
"""

from __future__ import annotations

import json
import os
import time
from threading import Lock
from typing import Optional, Tuple

# Pending permission requests expire after this many seconds to prevent
# a stale "yes" from accidentally granting access much later.
_PENDING_TIMEOUT_SECONDS: float = 300.0  # 5 minutes

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))   # core/
_PROJECT_ROOT = os.path.dirname(_MODULE_DIR)                # project root

# Use the writable DATA_DIR so granted folders persist after EXE install
try:
    from configs.settings import DATA_DIR as _DATA_DIR
    _STORE_FILE = os.path.join(str(_DATA_DIR), "granted_folders.json")
except Exception:
    _STORE_FILE = os.path.join(_PROJECT_ROOT, "data", "granted_folders.json")

# Canonical approve / deny vocabulary — used by handle_response() and by any
# UI layer that needs to display the supported words to the user.
APPROVE_WORDS: frozenset[str] = frozenset({
    "yes", "y", "allow", "ok", "okay", "sure", "grant", "grant access", "yep", "yup",
})
DENY_WORDS: frozenset[str] = frozenset({
    "no", "n", "deny", "nope", "nah", "cancel", "reject", "decline",
})

_lock = Lock()


def _load_from_disk() -> list[str]:
    """Load the persisted granted-folder list from JSON."""
    try:
        if os.path.exists(_STORE_FILE):
            with open(_STORE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    return [str(p) for p in data]
    except Exception:
        pass
    return []


def _save_to_disk(folders: list[str]) -> None:
    """Persist the granted-folder list to JSON."""
    try:
        os.makedirs(os.path.dirname(_STORE_FILE), exist_ok=True)
        with open(_STORE_FILE, "w", encoding="utf-8") as fh:
            json.dump(sorted(folders), fh, indent=2)
    except Exception:
        pass


class _PermissionStore:
    """Thread-safe dynamic folder permission store."""

    def __init__(self) -> None:
        self._granted: set[str] = set(_load_from_disk())
        # Static (built-in) folders — in-memory only, never persisted to disk.
        # Populated at access_control.py import time via grant_static().
        self._static_granted: set[str] = set()
        # Pending permission request: (folder_path, original_query, timestamp)
        self._pending_folder: Optional[str] = None
        self._pending_query: Optional[str] = None
        self._pending_ts: float = 0.0  # epoch seconds when request was stored

    # ── normalisation ────────────────────────────────────────────────────────

    @staticmethod
    def _norm(path: str) -> str:
        return os.path.normcase(os.path.normpath(path.strip()))

    # ── granted-folder management ────────────────────────────────────────────

    def is_granted(self, path: str) -> bool:
        """Return True if *path* (or a parent of it) is in the static or dynamic grant list."""
        norm = self._norm(path)
        with _lock:
            for g in self._static_granted | self._granted:
                norm_g = self._norm(g)
                # Exact match (the granted root itself) or proper sub-path only.
                if norm == norm_g or norm.startswith(norm_g + os.sep):
                    return True
        return False

    def grant(self, path: str) -> None:
        """Permanently grant access to *path* and persist to disk."""
        with _lock:
            self._granted.add(path.strip())
            _save_to_disk(list(self._granted))

    def grant_static(self, path: str) -> None:
        """Grant access to a built-in folder — in-memory only, never written to disk."""
        with _lock:
            self._static_granted.add(path.strip())

    def revoke(self, path: str) -> None:
        """Remove a previously-granted folder."""
        with _lock:
            self._granted.discard(path.strip())
            # Also try normalized variant
            norm = self._norm(path)
            to_remove = {f for f in self._granted if self._norm(f) == norm}
            self._granted -= to_remove
            _save_to_disk(list(self._granted))

    def get_granted_folders(self) -> list[str]:
        """Return all granted folders (static built-ins + user-approved runtime grants)."""
        with _lock:
            return list(self._static_granted | self._granted)

    # ── pending-request management ───────────────────────────────────────────

    def set_pending(self, folder: str, original_query: str) -> None:
        """Store a pending permission request with a timestamp."""
        with _lock:
            self._pending_folder = folder
            self._pending_query = original_query
            self._pending_ts = time.monotonic()

    def get_pending(self) -> Tuple[Optional[str], Optional[str]]:
        """Return (folder, original_query) for the pending request, or (None, None).

        Returns (None, None) and auto-clears if the request has expired.
        """
        with _lock:
            if self._pending_folder is None:
                return None, None
            age = time.monotonic() - self._pending_ts
            if age > _PENDING_TIMEOUT_SECONDS:
                # Stale — clear silently; caller should inform user
                self._pending_folder = None
                self._pending_query = None
                self._pending_ts = 0.0
                return None, None
            return self._pending_folder, self._pending_query

    def is_expired(self) -> bool:
        """Return True when there was a pending request that has now timed out."""
        with _lock:
            if self._pending_folder is None:
                return False
            return (time.monotonic() - self._pending_ts) > _PENDING_TIMEOUT_SECONDS

    def clear_pending(self) -> None:
        with _lock:
            self._pending_folder = None
            self._pending_query = None
            self._pending_ts = 0.0

    def has_pending(self) -> bool:
        """Return True only when there is a non-expired pending request."""
        with _lock:
            if self._pending_folder is None:
                return False
            if (time.monotonic() - self._pending_ts) > _PENDING_TIMEOUT_SECONDS:
                # Auto-expire
                self._pending_folder = None
                self._pending_query = None
                self._pending_ts = 0.0
                return False
            return True

    def handle_response(self, user_input: str) -> tuple:
        """Atomically handle a yes/no response to the current pending permission request.

        This is the single authoritative gate for permission responses.  It is
        intentionally atomic (single lock acquisition) so there is no window
        where a concurrent call could observe an inconsistent state.

        Returns
        -------
        A 3-tuple ``(action, folder, original_query)`` where *action* is one of:

        ``"GRANT"``
            User approved.  *folder* and *original_query* are set; access has
            already been persisted to disk before this returns.
        ``"DENY"``
            User denied.  Pending data has been cleared.
        ``"EXPIRED"``
            An approve/deny word was received but the pending request timed out.
            Pending data has been cleared.
        ``"NO_PENDING"``
            An approve/deny word was received but there was no pending request.
        ``"NONE"``
            *user_input* is not an approve/deny word — not a permission response.
        """
        normalized = user_input.strip().lower()
        is_approve = normalized in APPROVE_WORDS
        is_deny = normalized in DENY_WORDS

        if not is_approve and not is_deny:
            return "NONE", None, None

        with _lock:
            # Check expiry first (auto-clear)
            if self._pending_folder is not None:
                age = time.monotonic() - self._pending_ts
                if age > _PENDING_TIMEOUT_SECONDS:
                    self._pending_folder = None
                    self._pending_query = None
                    self._pending_ts = 0.0
                    return "EXPIRED", None, None

            if self._pending_folder is None:
                return "NO_PENDING", None, None

            if is_approve:
                folder = self._pending_folder
                query = self._pending_query
                # Persist the grant before clearing pending state
                self._granted.add(folder.strip())
                _save_to_disk(list(self._granted))
                self._pending_folder = None
                self._pending_query = None
                self._pending_ts = 0.0
                return "GRANT", folder, query

            # is_deny
            self._pending_folder = None
            self._pending_query = None
            self._pending_ts = 0.0
            return "DENY", None, None


# Module-level singleton
permission_store = _PermissionStore()
