import os

# Always resolve relative to the project root, not the CWD
_AGENT_KN_DIR = os.path.dirname(os.path.abspath(__file__))     # agents/knowledge
_PROJECT_ROOT  = os.path.dirname(os.path.dirname(_AGENT_KN_DIR))  # project root
try:
    from configs.settings import DATA_DIR as _DATA_DIR
    _DEFAULT_DOCS_PATH = os.path.join(str(_DATA_DIR), "documents")
except Exception:
    _DEFAULT_DOCS_PATH = os.path.join(_PROJECT_ROOT, "data", "documents")

# Only show user-created document files; exclude system/internal files
_DOCUMENT_EXTENSIONS = {
    '.pdf', '.pptx', '.csv', '.txt', '.docx', '.doc',
    '.xlsx', '.xls', '.png', '.jpg', '.jpeg', '.mp4', '.mp3', '.m4a',
    '.md', '.json', '.py', '.js', '.ts', '.java', '.html', '.xml',
    '.yaml', '.yml', '.ini', '.cfg',
}


def list_files_in_folder(folder_path: str) -> str:
    """Return a plain OS directory listing for *folder_path*.

    This is a direct ``os.listdir`` call — it does NOT use the vector store.
    Use this for 'show/list files' queries so the user sees every file that
    actually exists in the folder, not just what has been indexed.
    """
    try:
        if not os.path.isdir(folder_path):
            return f"Folder not found:\n\U0001f4c1 {folder_path}"
        entries = sorted(os.listdir(folder_path))
        files = [e for e in entries if os.path.isfile(os.path.join(folder_path, e))]
        subdirs = [e for e in entries if os.path.isdir(os.path.join(folder_path, e))]
        if not files and not subdirs:
            return f"No files found in:\n\U0001f4c1 {folder_path}"
        parts: list[str] = [f"\U0001f4c2 Files in folder: {folder_path}\n"]
        if subdirs:
            parts.append("\U0001f4c1 Subfolders:")
            parts.extend(f"  \u2022 {d}/" for d in subdirs)
            parts.append("")
        if files:
            parts.append("\U0001f4c4 Files:")
            parts.extend(f"  \u2022 {f}" for f in files)
        return "\n".join(parts)
    except PermissionError:
        return f"Permission denied reading folder:\n\U0001f4c1 {folder_path}"
    except Exception as exc:
        return f"Error reading folder: {exc}"


def _get_win_docs_path() -> str:
    """Return the authorized Windows docs path from settings, or empty string."""
    try:
        from configs.settings import settings
        return str(settings.windows_docs_path)
    except Exception:
        return ""


def list_all_documents(base_path=None, folder_path=None):
    lines: list[str] = []

    # ── Folder-scoped listing: when a specific folder_path is active ──────────
    # Show ALL files in the folder (no extension filter) so the user sees
    # exactly what is there — mirrors what list_files_in_folder() returns.
    if folder_path:
        if not os.path.isdir(folder_path):
            return (
                f"❌ Invalid folder path\n\n"
                f"I could not find this folder:\n"
                f"\U0001f4c1 {folder_path}\n\n"
                "Please check the spelling, folder name, and path."
            )
        return list_files_in_folder(folder_path)

    # ── Section 1: project-local docs (data/documents/) ──────────────────────
    local_path = base_path or _DEFAULT_DOCS_PATH
    if os.path.exists(local_path):
        local_files = [
            f for f in sorted(os.listdir(local_path))
            if os.path.isfile(os.path.join(local_path, f))
            and os.path.splitext(f)[1].lower() in _DOCUMENT_EXTENSIONS
        ]
        if local_files:
            lines.append(f"Project documents ({local_path}):")
            lines.extend(f"  • {f}" for f in local_files)

    # ── Section 2: authorized Windows docs (C:\AI_Test_Documents) ────────────
    win_path = _get_win_docs_path()
    if win_path and os.path.isdir(win_path) and win_path != local_path:
        win_files = [
            f for f in sorted(os.listdir(win_path))
            if os.path.isfile(os.path.join(win_path, f))
            and os.path.splitext(f)[1].lower() in _DOCUMENT_EXTENSIONS
        ]
        if win_files:
            if lines:
                lines.append("")  # blank separator
            lines.append(f"Authorized documents ({win_path}):")
            lines.extend(f"  • {f}" for f in win_files)

    # ── Section 3: dynamically granted folders ─────────────────────────────────
    try:
        from core.permission_store import permission_store as _ps
        _win_norm = os.path.normcase(os.path.normpath(win_path)) if win_path else ""
        _local_norm = os.path.normcase(os.path.normpath(local_path))
        for granted in sorted(_ps.get_granted_folders()):
            if not os.path.isdir(granted):
                continue
            _g_norm = os.path.normcase(os.path.normpath(granted))
            if _g_norm in (_win_norm, _local_norm):
                continue  # already listed
            granted_files = [
                f for f in sorted(os.listdir(granted))
                if os.path.isfile(os.path.join(granted, f))
                and os.path.splitext(f)[1].lower() in _DOCUMENT_EXTENSIONS
            ]
            if granted_files:
                if lines:
                    lines.append("")
                lines.append(f"Granted documents ({granted}):")
                lines.extend(f"  • {f}" for f in granted_files)
    except Exception:
        pass

    if not lines:
        return "No documents found."

    return "Here are your documents:\n" + "\n".join(lines)