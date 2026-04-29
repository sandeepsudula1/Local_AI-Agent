"""
core/runtime_paths.py
====================
Path resolution for both development and PyInstaller frozen executables.

Handles:
- Base application directory detection
- sys._MEIPASS handling for frozen executables
- Data directory location
- Tesseract path detection
- Environment variable overrides

Usage::

    from core.runtime_paths import get_app_root, get_data_dir, find_tesseract

    app_root = get_app_root()
    data_dir = get_data_dir()
    tesseract_path = find_tesseract()
"""

from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger

log = get_logger(__name__)


def is_frozen() -> bool:
    """Check if running as PyInstaller .exe (frozen executable).
    
    Returns
    -------
    True if running as .exe, False if running from source.
    """
    return getattr(sys, 'frozen', False)


def get_meipass() -> str:
    """Get PyInstaller _MEIPASS directory (temp folder for frozen app).
    
    Returns
    -------
    Path to _MEIPASS or empty string if not frozen.
    """
    return getattr(sys, '_MEIPASS', '')


def get_app_root() -> str:
    """Get application root directory.
    
    For frozen executables:
        Returns directory containing LocalAIAgent.exe
    
    For source code:
        Returns directory containing main.py
    
    Returns
    -------
    Absolute path to application root.
    """
    if is_frozen():
        # Frozen: sys.executable points to the .exe file
        app_root = os.path.dirname(sys.executable)
        log.debug(f"Running frozen - app root: {app_root}")
    else:
        # Source: find directory containing main.py
        current_file = os.path.abspath(__file__)  # This file
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
        app_root = project_root
        log.debug(f"Running from source - app root: {app_root}")
    
    return app_root


def get_data_dir(create: bool = True) -> str:
    """Get data directory path.
    
    Location:
    - Frozen: Same folder as .exe
    - Source: 'data' subfolder in project root
    
    Parameters
    ----------
    create:
        If True, create directory if it doesn't exist.
    
    Returns
    -------
    Absolute path to data directory.
    """
    if is_frozen():
        data_dir = os.path.join(get_app_root(), 'data')
    else:
        app_root = get_app_root()
        data_dir = os.path.join(app_root, 'data')
    
    if create and not os.path.exists(data_dir):
        try:
            os.makedirs(data_dir, exist_ok=True)
            log.debug(f"Created data directory: {data_dir}")
        except Exception as e:
            log.warning(f"Failed to create data directory: {e}")
    
    return data_dir


def get_bundled_resource(relative_path: str) -> Optional[str]:
    """Get path to resource bundled in .exe or data folder.
    
    For frozen executables, looks in _MEIPASS/resource_path.
    For source code, looks in data_dir/resource_path.
    
    Parameters
    ----------
    relative_path:
        Path relative to root (e.g., 'models/embedding.bin')
    
    Returns
    -------
    Full path if resource exists, None otherwise.
    """
    if is_frozen():
        # In frozen executable, look in bundle
        meipass = get_meipass()
        full_path = os.path.join(meipass, relative_path)
    else:
        # In source, look in data directory
        data_dir = get_data_dir()
        full_path = os.path.join(data_dir, relative_path)
    
    if os.path.exists(full_path):
        log.debug(f"Found bundled resource: {full_path}")
        return full_path
    
    return None


# ── Special binary/tool detection ────────────────────────────────────────

def find_tesseract() -> Optional[str]:
    """Find Tesseract-OCR executable.
    
    Checks (in order):
    1. TESSERACT_CMD environment variable
    2. Program Files (Windows default locations)
    3. System PATH
    
    Returns
    -------
    Full path to tesseract.exe if found, None otherwise.
    """
    
    # Check environment variable
    env_path = os.environ.get('TESSERACT_CMD')
    if env_path and os.path.exists(env_path):
        log.debug(f"Found Tesseract via env var: {env_path}")
        return env_path
    
    # Check common Windows installation paths
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%ProgramFiles%\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe"),
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            log.debug(f"Found Tesseract: {path}")
            return path
    
    # Check PATH
    found = shutil.which('tesseract')
    if found:
        log.debug(f"Found Tesseract on PATH: {found}")
        return found
    
    log.warning("Tesseract-OCR not found on system")
    return None


def find_poppler() -> Optional[str]:
    """Find Poppler (pdf2image helper).
    
    Checks:
    1. System PATH for pdftoppm
    2. System PATH for pdfimages
    
    Returns
    -------
    Full path to poppler tool if found, None otherwise.
    """
    for tool in ['pdftoppm', 'pdfimages']:
        found = shutil.which(tool)
        if found:
            log.debug(f"Found Poppler tool '{tool}': {found}")
            return found
    
    log.warning("Poppler not found on system PATH")
    return None


def find_ollama_base() -> Optional[str]:
    """Find Ollama installation directory.
    
    Checks:
    1. OLLAMA_HOME environment variable
    2. %APPDATA%\\ollama
    3. %LOCALAPPDATA%\\Programs\\Ollama
    
    Returns
    -------
    Path to Ollama directory if found, None otherwise.
    """
    
    # Check environment variable
    env_path = os.environ.get('OLLAMA_HOME')
    if env_path and os.path.exists(env_path):
        log.debug(f"Found Ollama via OLLAMA_HOME: {env_path}")
        return env_path
    
    # Check default Windows locations
    appdata = os.environ.get('APPDATA')
    if appdata:
        path = os.path.join(appdata, 'ollama')
        if os.path.exists(path):
            log.debug(f"Found Ollama: {path}")
            return path
    
    localappdata = os.environ.get('LOCALAPPDATA')
    if localappdata:
        path = os.path.join(localappdata, 'Programs', 'Ollama')
        if os.path.exists(path):
            log.debug(f"Found Ollama: {path}")
            return path
    
    log.warning("Ollama not found on system")
    return None


# ── Init check on import ─────────────────────────────────────────────────

def log_runtime_info() -> None:
    """Log runtime environment information (call once at startup)."""
    is_frz = is_frozen()
    log.info(f"Running mode: {'Frozen executable (.exe)' if is_frz else 'Source code'}")
    
    if is_frz:
        meipass = get_meipass()
        log.debug(f"_MEIPASS (bundle): {meipass}")
        log.debug(f"Executable: {sys.executable}")
    else:
        log.debug(f"Project root: {get_app_root()}")
    
    log.debug(f"Data directory: {get_data_dir()}")
    
    # Tool availability
    tes = find_tesseract()
    log.debug(f"Tesseract: {'Found' if tes else 'Not found'}")
    
    pop = find_poppler()
    log.debug(f"Poppler: {'Found' if pop else 'Not found'}")
    
    ollama_home = find_ollama_base()
    log.debug(f"Ollama: {'Found' if ollama_home else 'Not found'}")


__all__ = [
    'is_frozen',
    'get_meipass',
    'get_app_root',
    'get_data_dir',
    'get_bundled_resource',
    'find_tesseract',
    'find_poppler',
    'find_ollama_base',
    'log_runtime_info',
]
