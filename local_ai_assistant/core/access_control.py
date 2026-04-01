"""
core/access_control.py
=======================
Access control layer for document retrieval.

Intercepts queries BEFORE they reach the RAG pipeline and returns an
``AccessDecision`` telling the orchestrator exactly what to do.

Decision actions
-----------------
``PASS``               → proceed normally to RAG / intent pipeline
``BLOCK``              → send ``message`` to user; skip all RAG
``CLARIFY``            → ask the user a clarifying question; skip all RAG
``ALLOW_FOLDER``       → proceed to RAG but scope to ``folder_path``,
                         do NOT restrict to ``last_file``
``REQUEST_PERMISSION`` → ask the user to grant access to ``folder_path``;
                         orchestrator stores original query and waits for
                         a yes/no reply

Handled cases
-------------
1. Generic access / security / source question (no path, no content op)
   → BLOCK  "I use local files only from authorized folders."
2. Specific path in query
   a. Allowed or dynamically-granted path + content operation  → ALLOW_FOLDER
   b. Allowed or dynamically-granted path + access question    → BLOCK (affirmative)
   c. Unknown path + content operation  → REQUEST_PERMISSION  (ask user to grant)
   d. Unknown path + access question    → BLOCK (affirmative with note)
3. Global / system / restricted location request
   → BLOCK  "I cannot access all system files…"
4. (Handled inside Case 2a)
5. Folder follow-up ("that folder") — resolves from ``last_folder`` memory
   → ALLOW_FOLDER using ``last_folder``; or CLARIFY if no last_folder set
6. Vague folder reference without a concrete path
   → CLARIFY  "Which folder do you mean?"
7. Short ambiguous standalone folder reference (≤ 5 words)
   → CLARIFY  "Please clarify your request."
8. Bare filename in query but no folder context (e.g. "Explain sp.txt")
   → CLARIFY  "Which folder contains 'sp.txt'?"  (or ALLOW_FOLDER if last_folder known)

Public API
----------
``check_access_query(query, last_folder=None) -> AccessDecision``
``ALLOWED_FOLDERS``  — list of authorised directory paths
``resolve_folder_shortname(name)``  — bare folder name → full path

Notes
-----
- Pending permission requests expire after 5 minutes (``permission_store``).
- Root drives (``C:\\``, ``D:\\``) and Windows system directories are never
  requestable via ``REQUEST_PERMISSION`` — they always hard-BLOCK.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Authorised folders
# ---------------------------------------------------------------------------

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))  # core/
_PROJECT_ROOT = os.path.dirname(_MODULE_DIR)               # project root

ALLOWED_FOLDERS: list[str] = [
    "C:\\AI_Test_Documents",
    os.path.normpath(os.path.join(_PROJECT_ROOT, "data", "documents")),
]

# Pre-seed the built-in folders into the permission store so that
# _is_path_allowed can delegate entirely to permission_store.is_granted().
# These are registered as static (in-memory only, not persisted to disk).
try:
    from core.permission_store import permission_store as _ps_init
    for _sf in ALLOWED_FOLDERS:
        _ps_init.grant_static(_sf)
    del _ps_init
except Exception:
    pass

# ---------------------------------------------------------------------------
# AccessDecision
# ---------------------------------------------------------------------------

@dataclass
class AccessDecision:
    """Instruction returned by ``check_access_query`` to the orchestrator."""

    action: str        # "PASS" | "BLOCK" | "CLARIFY" | "ALLOW_FOLDER" | "REQUEST_PERMISSION"
    message: str = ""  # Non-empty for BLOCK, CLARIFY, REQUEST_PERMISSION
    folder_path: str = ""  # Non-empty for ALLOW_FOLDER and REQUEST_PERMISSION


# Singleton no-op — avoids allocating a new object on normal queries
_PASS = AccessDecision(action="PASS")

# ---------------------------------------------------------------------------
# Regex patterns (kept for fast-path classification and structural checks)
# ---------------------------------------------------------------------------

# Global / system / restricted location keywords (no-path queries only)
_GLOBAL_RESTRICT_RE = re.compile(
    r"\b("
    r"all\s+(system\s+)?(files?|documents?|data)"
    r"|entire\s+(system|computer|hard\s*drive|disk|file\s*system)"
    r"|every\s+(file|document)"
    r"|everywhere"
    r"|system\s+files?"
    r"|(?:my\s+)?(?:downloads?|desktop|pictures?|music|videos?)\s*folder\b"
    r")\b",
    re.IGNORECASE,
)

# Fast-path: ACCESS_CHECK — user is asking *about* access, not requesting content
_ACCESS_CHECK_FAST_RE = re.compile(
    r"\b("
    r"do\s+you\s+have\s+(?:the\s+)?(?:acces+t?|permis+ion|authoriz(?:ation|ation)|authoris(?:ation|ation))"
    r"|can\s+you\s+(?:access|see|view|use|open)"
    r"|are\s+you\s+(?:allowed|authorized|authorised|permitted)"
    r"|have\s+(?:any\s+)?acces+(?:t|to)?(?:\s+to)?"
    r"|(?:acces+t?|permis+ion)\s+to"
    r"|which\s+(?:folder|directory|path|location)\s+(?:do\s+you|are\s+you|can\s+you)"
    r"|what\s+(?:folder|directory|path|location)\s+(?:do\s+you|are\s+you|can\s+you)"
    r"|are\s+you\s+(?:reading|accessing|using)\s+(?:my|the)?\s*(?:files?|docs?|documents?|data)"
    r"|where\b.{0,40}\b(?:get|fetch|load|read|source|store|keep|come)\b.{0,25}\b(?:documents?|docs?|files?|data)"
    r"|where\s+(?:are|is)\s+.{0,15}(?:documents?|docs?|files?|data)"
    r")\b",
    re.IGNORECASE,
)

# Fast-path: CONTENT_QUERY — user wants to interact with content
_CONTENT_QUERY_FAST_RE = re.compile(
    r"\b("
    r"summarize|summarise|explain|describe|list|show|display|get\s+details?"
    r"|analyze|analyse|search|find|look\s+(for|at)|read|check|scan|fetch"
    r"|tell\s+me\s+about|what.{0,15}in|index|process|extract|review|open\s+file"
    r"|get\s+information|give\s+me|load|query"
    r"|take\s+permission|request\s+(access|permission)|need\s+(access|permission)"
    r"|grant\s+(me\s+)?access|get\s+access"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# NLU-based intent classifier
# ---------------------------------------------------------------------------

def _classify_access_intent(text: str) -> str:
    """Classify whether *text* is an ACCESS_CHECK, CONTENT_QUERY, or OTHER.

    Strategy
    --------
    1. Regex fast-path — covers the large majority of cases instantly.
    2. LLM fallback   — handles ambiguous / paraphrased phrasing that
       regex cannot reliably detect.

    Returns
    -------
    ``"ACCESS_CHECK"``
        User is asking *about* access/permissions — no file operation needed.
        e.g. "do you have access to C:\\Docs?", "which folders can you read?"
    ``"CONTENT_QUERY"``
        User wants to read, search, find, list, or operate on content.
        e.g. "find files in C:\\Docs", "summarize report.pdf"
    ``"OTHER"``
        Neither of the above.
    """
    # ── 1. Fast-path regex ────────────────────────────────────────────────
    if _ACCESS_CHECK_FAST_RE.search(text):
        # ACCESS_CHECK wins only when there is no stronger content-operation signal
        if not _CONTENT_QUERY_FAST_RE.search(text):
            return "ACCESS_CHECK"
    if _CONTENT_QUERY_FAST_RE.search(text):
        return "CONTENT_QUERY"

    # ── 2. LLM fallback for ambiguous phrasing ────────────────────────────
    try:
        import ollama
        from configs.settings import settings as _s
        resp = ollama.chat(
            model=_s.model_name,
            options={"temperature": 0.0, "num_predict": 10},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the user message into EXACTLY ONE label:\n"
                        "ACCESS_CHECK  — user is asking about access/permissions (no file operation).\n"
                        "CONTENT_QUERY — user wants to read, search, find, list, or process content.\n"
                        "OTHER         — neither.\n"
                        "Output ONLY the label. No explanation."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        label = resp.get("message", {}).get("content", "").strip().upper()
        label = re.split(r"[\s\n\r.,\-]+", label)[0]
        if label in ("ACCESS_CHECK", "CONTENT_QUERY"):
            return label
    except Exception:
        pass

    return "OTHER"

# "that folder" / "this folder" follow-up signals
_FOLDER_FOLLOWUP_RE = re.compile(
    r"\b("
    r"(that|this|the\s+same|above|previous|last)\s+(folder|directory|location|path)"
    r"|same\s+(folder|directory|location|path)"
    r")\b",
    re.IGNORECASE,
)

# Vague folder reference: demonstrative + folder-like word (no concrete path)
_VAGUE_FOLDER_RE = re.compile(
    r"\b(this|that|the|a)\s+(folder|directory|path|location)\b",
    re.IGNORECASE,
)

# Windows-style or Unix-style file path (supports spaces in path components)
_PATH_RE = re.compile(
    r'([A-Za-z]:\\[^\n"\'<>|?*,;]+|(?:/[\w.\-]+){2,})',
    re.IGNORECASE,
)

# Suffix pattern: English prose words that begin a trailing non-path clause.
# Starting from the first such trigger word, everything is stripped.
_PATH_TRAIL_RE = re.compile(
    r"\s+(?:this|that|the|a|an|here|there|above|below|please|now"
    r"|is|was|are|were|and|or|then|when|where|which"
    r"|for|from|in|at|location|place|path)\b.*$",
    re.IGNORECASE,
)

# Bare filename with a recognised extension (no leading path separator)
# Mirrors the pattern used by ``_extract_filename_from_query`` in retrieval_agent.
_FILENAME_RE = re.compile(
    r"\b([\w][\w\-\.]*\.(?:pdf|pptx|docx|txt|md|csv|xlsx|xls|png|jpg|jpeg"
    r"|py|js|ts|json|html|xml|ini|cfg|yaml|yml|webp))\b",
    re.IGNORECASE,
)

# Patterns that indicate a system-wide or root-level path that must never be
# offered as a REQUEST_PERMISSION candidate (always hard-BLOCK).
_SYSTEM_PATH_RE = re.compile(
    r"(?:"
    r"^[A-Za-z]:\\?$"                          # bare drive root: C:\ or C:
    r"|[A-Za-z]:\\(?:Windows|System32|Program\s*Files\S*|Users\\[^\\]+\\AppData)"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Email-operation passthrough
# ---------------------------------------------------------------------------
# Any input that is clearly an email reply/response action must bypass the
# access-control layer entirely so the intent classifier can handle it.
# The access-control NLU (especially small LLMs) misclassifies phrases like
# "give response" or "respond to this" as ACCESS_CHECK, causing a BLOCK.
_EMAIL_OP_PASSTHROUGH_RE = re.compile(
    r"\b("
    r"rep(?:ly|lies|lied|lying|ond|onds|onded|onding|onse|onses|pond|ponse|pond|ponding)"
    r"|respond|response|reply"
    r"|write\s+back"
    r"|write\s+(a\s+)?(reply|response|message)"
    r"|draft\s+(a\s+)?(reply|response|email|message)"
    r"|compose\s+(a\s+)?(reply|response|email|message)"
    r"|give\s+.{0,20}(reply|response|answer)"
    r"|tell\s+(him|her|them)"
    r"|send\s+(a\s+)?(reply|response)"
    r"|above\s+(mail|email|message)"
    r"|reply\s+to\s+(above|this|that|the)"
    r"|respond\s+to\s+(above|this|that|the)"
    r")\b",
    re.IGNORECASE,
)


def _is_system_path(path: str) -> bool:
    """Return True for root drives and sensitive Windows system paths."""
    return bool(_SYSTEM_PATH_RE.match(path.strip()))


def _normalize_windows_path(path: str) -> str:
    """Return the canonical on-disk path for *path*, fixing common mismatches.

    Currently handles:
    - ``C:\\Users\\X\\Desktop`` → ``C:\\Users\\X\\OneDrive\\Desktop``
      when the OneDrive variant exists (and the plain Desktop does not).
    - ``C:FolderName`` → ``C:\\FolderName`` (missing backslash after drive colon).
    - Leading/trailing quote characters (sometimes left by speech or copy-paste).
    - Leading/trailing spaces and forward-slash normalisation.
    """
    if not path:
        return path
    path = path.strip().strip('"').strip("'")
    # Fix forward slashes → backslashes on Windows paths
    if re.match(r'^[A-Za-z][:/\\]', path):
        path = path.replace('/', '\\')
    # Fix: C:FolderName → C:\FolderName (backslash missing after drive colon)
    m_missing = re.match(r'^([A-Za-z]):([^\\].+)$', path)
    if m_missing:
        path = m_missing.group(1) + ':\\' + m_missing.group(2)
    # OneDrive Desktop auto-correction
    # Matches any path containing \Users\<name>\Desktop (but NOT \OneDrive\)
    _od_re = re.compile(
        r'(?i)^([A-Za-z]:\\Users\\[^\\]+)\\(Desktop)\\?(.*)$'
    )
    m = _od_re.match(path)
    if m:
        prefix, leaf, suffix = m.group(1), m.group(2), m.group(3)
        onedrive_path = os.path.join(prefix, "OneDrive", leaf)
        if suffix:
            onedrive_path = os.path.join(onedrive_path, suffix)
        if os.path.exists(onedrive_path) and not os.path.exists(path):
            return onedrive_path
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_path(text: str) -> Optional[str]:
    """Return the first file-system path found in *text*, or ``None``.

    Supports paths with spaces in folder/file names (e.g. ``C:\\My Folder\\report.pdf``).
    Trailing prose words ("this location", "the folder", etc.) are stripped.

    Fallback tiers (in order):
    1. Standard Windows/Unix path (``[A-Za-z]:\\...`` or ``/unix/path``).
    2. Drive + colon, no backslash  — ``C:AI_Test_Documents5`` → ``C:\\AI_Test_Documents5``.
    3. Drive-letter typo  — ``CAI_Test_Documents5`` → ``C:\\AI_Test_Documents5``
       (only when the resolved path actually exists on disk).
    """
    match = _PATH_RE.search(text)
    if not match:
        # Fallback 1: C:FolderName (colon present, backslash missing)
        m_noslash = re.search(
            r'\b([A-Za-z]):([A-Za-z][A-Za-z0-9_\-]+(?:\\[A-Za-z0-9_\-\.]+)*)\b',
            text,
        )
        if m_noslash:
            raw = m_noslash.group(1) + ':\\' + m_noslash.group(2)
            raw = _PATH_TRAIL_RE.sub("", raw).rstrip("\\/ .,")
            if raw:
                return _normalize_windows_path(raw)
        # Fallback 2: DriveLetterFolderName typo (no colon, e.g. CAI_Test_Documents5)
        # Only fires when the rest contains at least one underscore (folder-name signal)
        # AND the resolved candidate actually exists on disk.
        m_typo = re.search(r'\b([A-Za-z])([A-Z][A-Za-z0-9_\-]{4,})\b', text)
        if m_typo and '_' in m_typo.group(2):
            candidate = f"{m_typo.group(1).upper()}:\\{m_typo.group(2)}"
            norm_cand = _normalize_windows_path(candidate)
            if os.path.exists(norm_cand):
                log.debug(
                    "[ACCESS_CTRL] Typo path resolved: %r → %r",
                    m_typo.group(0), norm_cand,
                )
                return norm_cand
        return None
    raw = match.group(1)
    # Strip trailing English prose ("above", "this location", "for review", …)
    raw = _PATH_TRAIL_RE.sub("", raw).rstrip("\\/ .,")
    if not raw:
        return None
    # Normalize common path mistakes (e.g. Desktop → OneDrive\Desktop)
    return _normalize_windows_path(raw)


def _extract_bare_folder_name(text: str) -> Optional[str]:
    """Detect bare folder names like 'AI_Test_Documents3' without drive letter.
    
    Returns the folder name if detected, or None.
    Used for access check questions where user references a folder by name only.
    """
    # Match folder names like AI_Test_Documents, AI_Test_Documents1, AI_Test_Documents3, etc.
    # Also matches paths like "AI_Test_Documents3 this location" (trailing prose stripped)
    bare_folder_match = re.search(
        r"\b(AI_Test_Documents\d*)\b",
        text,
        re.IGNORECASE
    )
    if bare_folder_match:
        return bare_folder_match.group(1)
    return None


def _is_path_allowed(path: str) -> bool:
    """Return ``True`` when *path* is under a static or user-granted allowed root.

    Delegates entirely to ``permission_store.is_granted()`` which covers both
    built-in static folders (seeded at module load) and runtime-granted folders.
    Falls back to a direct ``ALLOWED_FOLDERS`` scan if the store is unavailable.
    """
    try:
        from core.permission_store import permission_store
        return permission_store.is_granted(path)
    except Exception:
        pass
    # Fallback when permission_store is unavailable
    norm = os.path.normcase(os.path.normpath(path))
    for folder in ALLOWED_FOLDERS:
        norm_folder = os.path.normcase(os.path.normpath(folder))
        if norm == norm_folder or norm.startswith(norm_folder + os.sep):
            return True
    return False


def _allowed_folders_list() -> str:
    """Format the currently-permitted folders as a bullet string."""
    try:
        from core.permission_store import permission_store
        folders = sorted(permission_store.get_granted_folders())
        if folders:
            return "\n".join(f"  \u2022 {f}" for f in folders)
    except Exception:
        pass
    return "\n".join(f"  • {f}" for f in ALLOWED_FOLDERS)


def _decide_for_path(path: str, has_content_op: bool, is_access_q: bool) -> AccessDecision:
    """Return the correct decision when a concrete path was found in the query."""
    if not _is_path_allowed(path):
        # Hard block for root drives and sensitive system directories—never requestable
        if _is_system_path(path):
            return AccessDecision(
                action="BLOCK",
                message=(
                    f"I cannot access '{path}'. "
                    "Root drives and system directories are never accessible."
                ),
            )
        if has_content_op and not is_access_q:
            # Validate the path EXISTS on disk before asking for permission.
            # Asking the user to grant access to a typo or non-existent folder
            # is confusing and should be caught here instead.
            if not os.path.exists(path):
                return AccessDecision(
                    action="BLOCK",
                    message=(
                        f"\u274c Invalid folder path\n\n"
                        f"I could not find this folder on disk:\n"
                        f"\U0001f4c1 {path}\n\n"
                        f"Please check:\n"
                        f"- Spelling and folder name\n"
                        f"- Whether it\'s under OneDrive (e.g. "
                        f"\\OneDrive\\Desktop instead of \\Desktop)\n"
                        f"- That the folder actually exists"
                    ),
                )
            # Unknown folder + content operation → ask user to grant access
            return AccessDecision(
                action="REQUEST_PERMISSION",
                folder_path=path,
                message=(
                    f"🔒 Access Required\n\n"
                    f"The system does not currently have permission to access:\n\n"
                    f"📁 {path}\n\n"
                    f"To proceed, please confirm whether you would like to grant "
                    f"access to this folder.\n\n"
                    f"Type **yes** to allow access or **no** to cancel."
                ),
            )
        # Access/permission question on an unknown path → informative denial
        return AccessDecision(
            action="BLOCK",
            message=(
                f"I currently do not have access to:\n📁 {path}\n\n"
                f"To grant access, ask me to read, search, or summarize "
                f"files from it and I’ll request your permission."
            ),
        )
    if has_content_op and not is_access_q:
        # Content operation on an allowed path → scope retrieval to that folder
        return AccessDecision(action="ALLOW_FOLDER", folder_path=path)
    # Access/permission question on an allowed path → affirmative answer
    return AccessDecision(
        action="BLOCK",
        message=f"\u2705 Yes, I have access to that location:\n\U0001f4c1 {path}",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_access_query(
    query: str,
    last_folder: Optional[str] = None,
) -> AccessDecision:
    """Inspect *query* and return an ``AccessDecision``.

    Parameters
    ----------
    query:
        Raw user input.
    last_folder:
        Folder path stored from the previous turn (``ConversationMemory``).
        Used to resolve "that folder" follow-ups (Case 5).
    """
    text = query.strip()

    # ── Email-operation fast-exit ─────────────────────────────────────────────
    # Reply/response/compose phrases are email actions, not file-system queries.
    # Return PASS immediately so the intent classifier handles them correctly.
    # Without this, the access-control NLU misclassifies them and returns BLOCK.
    if _EMAIL_OP_PASSTHROUGH_RE.search(text):
        log.debug("[ACCESS_CTRL] email-op passthrough (PASS): %.60s", text)
        return _PASS

    # ── Step 1: Extract concrete path first ────────────────────────────────
    # Always done before any pattern matching so that path components like
    # "Desktop" or "Downloads" inside a real Windows path are never matched
    # by the global-restrict or intent patterns below.
    path = _extract_path(text)

    # ── Step 2: Classify intent with NLU (regex fast-path + LLM fallback) ──
    access_intent = _classify_access_intent(text)
    is_access_q   = (access_intent == "ACCESS_CHECK")
    has_content_op = (access_intent == "CONTENT_QUERY")

    # ── Step 3: Path-specific decision ─────────────────────────────────────
    if path is not None:
        return _decide_for_path(path, has_content_op, is_access_q)

    # ── Step 4: Global / system / restricted location (no path found) ───────
    # Only reached when no concrete path was in the query.
    _any_verb = bool(re.search(
        r"\b(can|could|do|are|have|will|would|please)\b", text, re.IGNORECASE
    ))
    if _GLOBAL_RESTRICT_RE.search(text) and (is_access_q or has_content_op or _any_verb):
        return AccessDecision(
            action="BLOCK",
            message=(
                "I cannot access your entire system at once. "
                "Please specify a folder path and I will request your permission if needed."
            ),
        )

    # ── Case 5 + 6: Folder follow-up or vague folder reference (no path) ───
    is_folder_followup = bool(_FOLDER_FOLLOWUP_RE.search(text))
    is_vague_folder = bool(_VAGUE_FOLDER_RE.search(text) and has_content_op)
    if is_folder_followup or is_vague_folder:
        if last_folder:
            return AccessDecision(action="ALLOW_FOLDER", folder_path=last_folder)
        return AccessDecision(
            action="CLARIFY",
            message="Which folder do you mean?",
        )

    # ── Case 8: Bare filename in query but no folder context ─────────────────
    # e.g. "Explain sp.txt", "summarize report.pdf"
    # When no full path is present and no folder is active we cannot safely
    # scope the retrieval — ask the user to specify a folder first.
    # When *last_folder* is already known we scope to that folder directly.
    _file_match = _FILENAME_RE.search(text)
    if _file_match and path is None:
        _file_name = _file_match.group(1)
        if last_folder:
            return AccessDecision(action="ALLOW_FOLDER", folder_path=last_folder)
        return AccessDecision(
            action="CLARIFY",
            message=f"Which folder contains '{_file_name}'?",
        )

    # ── Case 8b: Bare folder name (e.g. "AI_Test_Documents3") ────────────────
    # When user asks about access to a folder by name only (no C:\ drive letter),
    # check if that folder is accessible.
    bare_folder_name = _extract_bare_folder_name(text)
    if bare_folder_name and is_access_q:
        # Try both the bare name and with C:\ drive
        possible_paths = [
            f"C:\\{bare_folder_name}",
            bare_folder_name,
        ]
        for check_path in possible_paths:
            if _is_path_allowed(check_path):
                return AccessDecision(
                    action="BLOCK",
                    message=f"✅ Yes, I have access to that location:\n📁 {check_path}",
                )
        # Folder name not in allowed list — deny access
        return AccessDecision(
            action="BLOCK",
            message=(
                f"I currently do not have access to:\n📁 C:\\{bare_folder_name}\n\n"
                f"To grant access, ask me to read, search, or summarize "
                f"files from this folder and I'll request your permission."
            ),
        )

    # ── Case 1: Generic access / source question (no path, no content op) ──
    if is_access_q:
        return AccessDecision(
            action="BLOCK",
            message=(
                "I don't currently have access to any specific folder.\n\n"
                "You can grant me access at any time — just ask me to read, "
                "search, or summarize files from any folder and I\u2019ll request "
                "your permission first."
            ),
        )

    # ── Case 7: Ambiguous standalone folder reference with no path ──────────
    # e.g. "the folder", "which folder?", "show folder" (≤ 5 words)
    # Cases 5/6 already handled follow-up / vague-with-content-op references.
    if (
        not is_folder_followup
        and not is_vague_folder
        and path is None
        and len(text.split()) <= 5
        and re.search(r"\bfolders?\b", text, re.IGNORECASE)
    ):
        return AccessDecision(action="CLARIFY", message="Please clarify your request.")

    return _PASS


# ---------------------------------------------------------------------------
# Folder shortname resolver
# ---------------------------------------------------------------------------

def resolve_folder_shortname(name: str) -> Optional[str]:
    """Return the full allowed folder path for a bare name or partial path.

    Checks both static ``ALLOWED_FOLDERS`` and dynamically granted folders.

    Handles inputs like:
    - ``"AI_Test_Documents"``           → ``"C:\\AI_Test_Documents"``
    - ``"ai test documents"``           → ``"C:\\AI_Test_Documents"``  (space variant)
    - ``"AI_Test_Documents2"``          → ``"C:\\AI_Test_Documents2"``  (granted)
    - ``"C:\\AI_Test_Documents"``       → ``"C:\\AI_Test_Documents"``
    - ``"documents"``                   → ``".../data/documents"``

    Returns ``None`` if *name* does not correspond to any allowed folder.
    """
    name = name.strip().strip("\\/").strip()
    if not name:
        return None
    norm_name = os.path.normcase(name)
    # Normalize query to lowercase with underscores for alias matching
    alias_name = name.lower().replace(" ", "_").replace("-", "_")

    # Build the candidate list from permission_store (includes static + user-granted)
    try:
        from core.permission_store import permission_store
        candidates: list[str] = permission_store.get_granted_folders()
    except Exception:
        candidates = list(ALLOWED_FOLDERS)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for f in candidates:
        key = os.path.normcase(os.path.normpath(f))
        if key not in seen:
            seen.add(key)
            unique_candidates.append(f)

    for folder in unique_candidates:
        # 1. Exact match after normalisation
        if os.path.normcase(os.path.normpath(folder)) == os.path.normcase(os.path.normpath(name)):
            return folder
        tail = os.path.basename(os.path.normpath(folder))
        # 2. Tail (basename) exact match — e.g. "AI_Test_Documents" → "C:\AI_Test_Documents"
        if os.path.normcase(tail) == norm_name:
            return folder
        # 3. Alias match: normalize both sides (lowercase + underscores) for flexible matching
        #    e.g. "ai test documents" or "AI-Test-Documents" → "C:\AI_Test_Documents"
        tail_alias = tail.lower().replace(" ", "_").replace("-", "_")
        if tail_alias == alias_name:
            return folder
        # 4. Substring containment: user's query is a sub-part of the folder name only.
        #    e.g. "test_documents" query → matches "AI_Test_Documents" folder.
        #    Deliberately single-direction: alias_name IN tail_alias (not the reverse)
        #    to prevent "AI_Test_Documents" from matching a query of "AI_Test_Documents2".
        if alias_name in tail_alias and len(alias_name) >= 4:
            return folder
    return None
