"""
core/dependency_checker.py
===========================
Runtime dependency validation for production deployment.

Checks for:
- Critical Python packages (packaging, transformers, etc.)
- System binaries (Tesseract, Poppler, Ollama)
- File paths and permissions
- Environment variables

Usage::

    from core.dependency_checker import check_all_dependencies, DependencyIssue

    issues = check_all_dependencies()
    if issues:
        for issue in issues:
            print(f"⚠️  {issue.level}: {issue.message}")
        if any(i.critical for i in issues):
            sys.exit(1)
"""

from __future__ import annotations

import os
import sys
import subprocess
import shutil
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

from core.logging_config import get_logger

log = get_logger(__name__)


class DependencyLevel(Enum):
    """Severity levels for dependency issues."""
    CRITICAL = "CRITICAL"     # App cannot run
    ERROR = "ERROR"            # Feature will not work
    WARNING = "WARNING"        # Feature degraded
    INFO = "INFO"              # Informational


@dataclass
class DependencyIssue:
    """Single dependency issue report."""
    level: DependencyLevel
    category: str
    message: str
    suggestion: str = ""
    
    @property
    def critical(self) -> bool:
        return self.level in (DependencyLevel.CRITICAL, DependencyLevel.ERROR)
    
    def __str__(self) -> str:
        s = f"[{self.level.value}] {self.category}: {self.message}"
        if self.suggestion:
            s += f"\n  💡 Suggestion: {self.suggestion}"
        return s


class DependencyChecker:
    """Check for missing dependencies and system requirements."""
    
    def __init__(self):
        self.issues: List[DependencyIssue] = []
        self.is_frozen = getattr(sys, 'frozen', False)  # Running as .exe?
    
    def add_issue(
        self,
        level: DependencyLevel,
        category: str,
        message: str,
        suggestion: str = ""
    ) -> None:
        """Record a dependency issue."""
        self.issues.append(DependencyIssue(
            level=level,
            category=category,
            message=message,
            suggestion=suggestion
        ))
    
    # ── Python package checks ────────────────────────────────────────────
    
    def check_python_packages(self) -> None:
        """Verify critical Python packages are available."""
        critical_packages = {
            'packaging': (
                'Required by transformers for version comparison',
                'pip install packaging'
            ),
            'transformers': (
                'Required for embedding models and LLM inference',
                'pip install transformers'
            ),
            'sentence_transformers': (
                'Required for text embeddings',
                'pip install sentence-transformers'
            ),
            'torch': (
                'Required for model inference',
                'pip install torch'
            ),
            'chromadb': (
                'Required for vector store',
                'pip install chromadb'
            ),
            'langchain': (
                'Required for RAG pipeline',
                'pip install langchain'
            ),
        }
        
        for pkg_name, (desc, install_cmd) in critical_packages.items():
            try:
                __import__(pkg_name)
                log.debug(f"✓ Python package '{pkg_name}' available")
            except ImportError as e:
                self.add_issue(
                    level=DependencyLevel.CRITICAL,
                    category="Python Package",
                    message=f"Missing '{pkg_name}': {desc}",
                    suggestion=install_cmd
                )
    
    def check_submodules(self) -> None:
        """Check for critical submodules that might be missing in PyInstaller builds."""
        critical_submodules = [
            ('packaging.version', 'Packaging version utilities'),
            ('packaging.specifiers', 'Version specifiers'),
            ('transformers.models.auto', 'Auto model loading'),
            ('transformers.utils', 'Transformers utilities'),
            ('tokenizers', 'Tokenizer library (C extension)'),
            ('safetensors', 'Safe tensor serialization'),
            ('huggingface_hub', 'HuggingFace hub integration'),
            ('google.auth', 'Google authentication'),
        ]
        
        for module_path, desc in critical_submodules:
            try:
                parts = module_path.split('.')
                mod = __import__(module_path)
                for part in parts[1:]:
                    mod = getattr(mod, part)
                log.debug(f"✓ Submodule '{module_path}' available")
            except (ImportError, AttributeError) as e:
                self.add_issue(
                    level=DependencyLevel.ERROR,
                    category="Python Submodule",
                    message=f"Missing or broken submodule '{module_path}': {desc}",
                    suggestion=(
                        f"Ensure {module_path.split('.')[0]} is installed. "
                        "This may indicate a PyInstaller packaging issue."
                    )
                )
    
    # ── System binary checks ─────────────────────────────────────────────
    
    def check_tesseract(self) -> None:
        """Check for Tesseract-OCR installation."""
        # Common installation paths on Windows
        tesseract_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%ProgramFiles%\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe"),
        ]
        
        # Check environment variable
        env_path = os.environ.get('TESSERACT_CMD')
        if env_path:
            tesseract_paths.insert(0, env_path)
        
        found = False
        for path in tesseract_paths:
            try:
                if os.path.exists(path):
                    # Verify it's executable
                    result = subprocess.run(
                        [path, '--version'],
                        capture_output=True,
                        timeout=2,
                        text=True
                    )
                    if result.returncode == 0:
                        log.info(f"Tesseract found: {path}")
                        found = True
                        break
            except Exception:
                pass
        
        if not found:
            self.add_issue(
                level=DependencyLevel.WARNING,
                category="System Binary - Tesseract-OCR",
                message=(
                    "Tesseract-OCR not found on system. "
                    "OCR fallback for PDFs and images will not work."
                ),
                suggestion=(
                    "Download and install Tesseract-OCR from:\n"
                    "  https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe\n"
                    "Or set TESSERACT_CMD environment variable to the executable path."
                )
            )
    
    def check_poppler(self) -> None:
        """Check for Poppler (required for pdf2image)."""
        # Poppler is usually on PATH as pdftoppm or similar
        poppler_tools = ['pdftoppm', 'pdfimages']
        
        found = False
        for tool in poppler_tools:
            if shutil.which(tool):
                log.info(f"Poppler tool found: {tool}")
                found = True
                break
        
        if not found:
            self.add_issue(
                level=DependencyLevel.WARNING,
                category="System Binary - Poppler",
                message=(
                    "Poppler not found on system PATH. "
                    "PDF to image conversion will not work."
                ),
                suggestion=(
                    "Download Poppler from:\n"
                    "  https://github.com/oschwartz10612/poppler-windows/releases\n"
                    "Extract and add the 'bin' folder to your system PATH."
                )
            )
    
    def check_ollama(self) -> None:
        """Check if Ollama is available (optional but recommended)."""
        try:
            import ollama
            
            # Try to reach Ollama daemon
            try:
                ollama.list()  # Quick health check
                log.info("Ollama daemon is running")
                return
            except Exception as e:
                log.debug(f"Ollama daemon not reachable: {e}")
                self.add_issue(
                    level=DependencyLevel.INFO,
                    category="System Service - Ollama",
                    message=(
                        "Ollama package is installed but daemon is not running. "
                        "LLM inference will not work."
                    ),
                    suggestion=(
                        "Start Ollama:\n"
                        "  1. Download from https://ollama.ai\n"
                        "  2. Run: ollama serve\n"
                        "  3. Or install as Windows service (see Ollama docs)"
                    )
                )
        except ImportError:
            self.add_issue(
                level=DependencyLevel.INFO,
                category="System Service - Ollama",
                message=(
                    "Ollama not installed. "
                    "LLM inference will not be available."
                ),
                suggestion=(
                    "Download and install from https://ollama.ai"
                )
            )
    
    # ── Data directory checks ────────────────────────────────────────────
    
    def check_data_directories(self) -> None:
        """Verify required data directories exist and are writable."""
        data_dir = 'data'
        
        if not os.path.exists(data_dir):
            self.add_issue(
                level=DependencyLevel.ERROR,
                category="Data Directory",
                message=f"Data directory '{data_dir}' does not exist",
                suggestion=f"Create: mkdir {data_dir}"
            )
            return
        
        # Check subdirectories
        required_subdirs = [
            'documents',
            'logs',
            'vector_store',
        ]
        
        for subdir in required_subdirs:
            path = os.path.join(data_dir, subdir)
            if not os.path.exists(path):
                try:
                    os.makedirs(path, exist_ok=True)
                    log.debug(f"Created data directory: {path}")
                except Exception as e:
                    self.add_issue(
                        level=DependencyLevel.ERROR,
                        category="Data Directory",
                        message=f"Cannot create '{path}': {e}",
                        suggestion="Check folder permissions"
                    )
    
    # ── Environment variable checks ──────────────────────────────────────
    
    def check_env_variables(self) -> None:
        """Check for recommended environment variables."""
        optional_vars = {
            'TESSERACT_CMD': 'Path to tesseract.exe',
            'WINDOWS_DOCS_PATH': 'Windows Documents folder to index',
            'LOG_LEVEL': 'Logging level (debug, info, warning, error)',
        }
        
        for var, desc in optional_vars.items():
            if var in os.environ:
                log.debug(f"✓ Env var '{var}' is set")
            else:
                log.debug(f"ℹ Env var '{var}' not set ({desc})")
    
    # ── Main check function ──────────────────────────────────────────────
    
    def check_all(self, verbose: bool = False) -> List[DependencyIssue]:
        """Run all dependency checks.
        
        Parameters
        ----------
        verbose:
            If True, log every check (not just issues).
        
        Returns
        -------
        List of issues found. Empty list if all checks pass.
        """
        self.issues = []
        
        log.info("Starting dependency checks...")
        
        try:
            self.check_python_packages()
            self.check_submodules()
            self.check_tesseract()
            self.check_poppler()
            self.check_ollama()
            self.check_data_directories()
            self.check_env_variables()
        except Exception as e:
            log.exception(f"Error during dependency checks: {e}")
            self.add_issue(
                level=DependencyLevel.WARNING,
                category="Dependency Checker",
                message=f"Error running checks: {e}"
            )
        
        # Log summary
        critical_count = sum(1 for i in self.issues if i.critical)
        warning_count = sum(1 for i in self.issues if i.level == DependencyLevel.WARNING)
        info_count = sum(1 for i in self.issues if i.level == DependencyLevel.INFO)
        
        if not self.issues:
            log.info("All dependency checks passed!")
        else:
            log.warning(
                f"Dependency check complete: "
                f"{critical_count} critical, {warning_count} warnings, {info_count} info"
            )
        
        return self.issues


# ── Convenience functions ────────────────────────────────────────────────────

_checker = None


def get_checker() -> DependencyChecker:
    """Get or create the global dependency checker."""
    global _checker
    if _checker is None:
        _checker = DependencyChecker()
    return _checker


def check_all_dependencies(verbose: bool = False) -> List[DependencyIssue]:
    """Check all dependencies and return issues.
    
    Parameters
    ----------
    verbose:
        If True, log detailed output.
    
    Returns
    -------
    List of dependency issues (empty if all pass).
    """
    return get_checker().check_all(verbose=verbose)


def require_package(package_name: str, description: str = "") -> bool:
    """Check if a package is available, log error if not.
    
    Parameters
    ----------
    package_name:
        Python package name (e.g., 'transformers').
    description:
        What the package is used for.
    
    Returns
    -------
    True if package is available, False otherwise.
    """
    try:
        __import__(package_name)
        return True
    except ImportError:
        msg = f"Missing package '{package_name}'"
        if description:
            msg += f" ({description})"
        log.error(msg)
        return False


def require_binary(binary_name: str, description: str = "", paths: list = None) -> Optional[str]:
    """Find a system binary on PATH or custom paths.
    
    Parameters
    ----------
    binary_name:
        Name of executable (e.g., 'tesseract').
    description:
        What the binary does.
    paths:
        Additional paths to search (beyond system PATH).
    
    Returns
    -------
    Full path to binary if found, None otherwise.
    """
    # Check system PATH
    found = shutil.which(binary_name)
    if found:
        log.debug(f"Found binary '{binary_name}': {found}")
        return found
    
    # Check custom paths
    if paths:
        for path in paths:
            if os.path.exists(path):
                log.debug(f"Found binary '{binary_name}': {path}")
                return path
    
    msg = f"Binary '{binary_name}' not found"
    if description:
        msg += f" ({description})"
    log.warning(msg)
    return None
