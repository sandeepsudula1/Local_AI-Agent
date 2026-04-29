"""
core/runtime_monitor.py
========================
Watchdog system for runtime health and graceful degradation.
"""

import threading
import time
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from core.logging_config import get_logger
from configs.settings import settings, DATA_DIR

log = get_logger(__name__)

@dataclass
class SystemStatus:
    ollama_ready: bool = False
    model_ready: bool = False
    storage_ready: bool = False
    tesseract_ready: bool = False
    poppler_ready: bool = False
    last_check_ts: float = 0.0
    failures: List[str] = field(default_factory=list)

class RuntimeMonitor:
    """Watchdog for system health during execution."""
    
    def __init__(self):
        self.status = SystemStatus()
        self.features = {
            "llm": True,
            "ocr": True,
            "pdf": True
        }
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Start the background watchdog thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="RuntimeWatchdog")
            self._thread.start()
            log.info("[MONITOR] Runtime Watchdog started.")

    def stop(self):
        """Stop the background watchdog thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            log.info("[MONITOR] Runtime Watchdog stopped.")

    def _monitor_loop(self):
        """Background loop for periodic health checks."""
        # Initial check
        self._perform_checks()
        
        while not self._stop_event.is_set():
            # Wait for 30 seconds or until stopped
            if self._stop_event.wait(30.0):
                break
            self._perform_checks()

    def _perform_checks(self):
        """Execute deep health checks and update feature flags."""
        failures = []
        
        # 1. Ollama & Model Check
        try:
            import ollama
            try:
                # Ping check
                models_list = ollama.list()
                self.status.ollama_ready = True
                
                model_name = settings.model_name
                # Modern library returns a ListModelsResponse object
                models = getattr(models_list, 'models', [])
                found = any(getattr(m, 'model', '').startswith(model_name) for m in models)
                
                if not found:
                    failures.append(f"Model '{model_name}' missing from Ollama.")
                    self.status.model_ready = False
                else:
                    self.status.model_ready = True
                    
            except Exception as e:
                failures.append(f"Ollama service unreachable: {e}")
                self.status.ollama_ready = False
                self.status.model_ready = False
        except ImportError:
            failures.append("Ollama python library missing.")
            self.status.ollama_ready = False

        # 2. Storage Check
        try:
            test_file = DATA_DIR / ".runtime_check"
            test_file.touch()
            test_file.unlink()
            self.status.storage_ready = True
        except Exception as e:
            failures.append(f"Storage not writable: {e}")
            self.status.storage_ready = False

        # 3. Binary Checks (Static but checked for PATH changes)
        self.status.tesseract_ready = bool(shutil.which("tesseract") or os.path.exists(settings.tesseract_cmd))
        self.status.poppler_ready = any(shutil.which(tool) for tool in ["pdftoppm", "pdfinfo"])

        # 4. Update Feature Flags (Graceful Degradation)
        with self._lock:
            self.features["llm"] = self.status.ollama_ready and self.status.model_ready
            self.features["ocr"] = self.status.tesseract_ready
            self.features["pdf"] = self.status.poppler_ready

        self.status.failures = failures
        self.status.last_check_ts = time.time()
        
        if failures:
            log.warning("[MONITOR] Runtime health issues detected: %s", failures)

    def is_feature_enabled(self, feature: str) -> bool:
        """Check if a specific feature is currently healthy and enabled."""
        with self._lock:
            return self.features.get(feature, False)

    def get_status_summary(self) -> str:
        """Return a human-readable summary of the current system health."""
        with self._lock:
            s = "### Runtime Health Status\n"
            s += f"- **LLM Service**: {'✅ OK' if self.features['llm'] else '❌ DOWN'}\n"
            s += f"- **OCR (Tesseract)**: {'✅ OK' if self.features['ocr'] else '⚠️ DISABLED (Not found)'}\n"
            s += f"- **PDF (Poppler)**: {'✅ OK' if self.features['pdf'] else '⚠️ DISABLED (Not found)'}\n"
            s += f"- **Storage**: {'✅ OK' if self.status.storage_ready else '❌ READ-ONLY / ACCESS DENIED'}\n"
            
            if self.status.failures:
                s += "\n**Recent Failures:**\n"
                for f in self.status.failures:
                    s += f"- {f}\n"
            
            if not self.features["llm"]:
                s += "\n**Action Required:** Ollama is not responding. Please ensure it is running (`ollama serve`)."
            
            return s

# Global singleton
runtime_monitor = RuntimeMonitor()
