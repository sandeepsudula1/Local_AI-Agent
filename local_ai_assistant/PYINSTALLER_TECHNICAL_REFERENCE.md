# PyInstaller Packaging Issues - Technical Reference

## Overview of Fixes Applied

This document explains the specific PyInstaller packaging issues encountered and how they were resolved.

---

## Issue 1: Missing "packaging" Module

### Problem

**Error Message:**
```
ModuleNotFoundError: No module named 'packaging'
```

**When it occurs:**
- During embedding model initialization
- When `transformers` or `huggingface_hub` tries to compare package versions

**Root Cause:**
- `packaging` is an indirect dependency of `transformers`
- It's used for version comparison logic (`packaging.version.parse()`, `packaging.specifiers`)
- PyInstaller's automatic dependency detection misses it because:
  1. It's not directly imported in main code
  2. It's loaded dynamically by entry points
  3. It's a compiled extension in some cases

### Solution Applied

**In `LocalAIAgent_FIXED.spec`:**
```python
hiddenimports=[
    'packaging',
    'packaging.version',
    'packaging.specifiers',
    'packaging.markers',
    'packaging.requirements',
    'packaging.tags',
    # ... rest of imports
]
```

**In `engines/embedding_engine.py`:**
Added pre-load checks:
```python
# Pre-checks for critical dependencies
critical_deps = ['packaging', 'transformers', 'tokenizers', 'safetensors']
for dep in critical_deps:
    try:
        __import__(dep)
    except ImportError as e:
        self._error = (
            f"Missing critical dependency '{dep}' required for embeddings. "
            f"This usually indicates a PyInstaller packaging issue."
        )
        return False
```

**Why this works:**
- `packaging` is now included in the bundled executable
- Pre-checks provide clear error messages if it's still missing
- Falls back gracefully without crashing

---

## Issue 2: Missing Tesseract-OCR System Binary

### Problem

**Error Messages:**
```
"pytesseract requires Tesseract-OCR installed"
FileNotFoundError: [Errno 2] No such file or directory: 'tesseract'
```

**When it occurs:**
- When trying to process scanned PDFs
- When processing image files
- When attempting OCR on document pages

**Root Cause:**
- Tesseract is a SYSTEM BINARY, not a Python package
- PyInstaller can't bundle it (it's a standalone Windows executable)
- The hardcoded path `C:\Program Files\Tesseract-OCR\tesseract.exe` may not exist on target machine
- If not installed, pytesseract raises an error

### Solution Applied

**Created `services/ocr_service.py`:**
```python
class OCRService:
    def check_availability(self) -> None:
        # Checks multiple locations for Tesseract
        # Sets self.is_available = True only if found
        
    def find_tesseract(self) -> Optional[str]:
        # 1. Check TESSERACT_CMD env variable
        # 2. Check common Windows paths
        # 3. Search system PATH
        # Returns path or None
```

**Created `core/runtime_paths.py`:**
```python
def find_tesseract() -> Optional[str]:
    """Find Tesseract on system."""
    # Checks env var, common paths, system PATH
    # Returns full path or None

def find_poppler() -> Optional[str]:
    """Find Poppler tools (pdf2image dependency)."""
    # Looks for pdftoppm on PATH
```

**Updated `agents/knowledge/retrieval_agent.py`:**
```python
# Before: Hard-coded path, crashes if not found
tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = tesseract_path

# After: Graceful fallback
from services.ocr_service import ocr_service
if not ocr_service.is_available:
    log.warning(f"OCR unavailable: {ocr_service.unavailable_reason}")
    return fallback_text
text = ocr_service.ocr_pil_image(image)
```

**Why this works:**
- Graceful degradation: app works without Tesseract (just no OCR)
- Clear error messages guide users to install Tesseract
- Supports both hardcoded paths and environment variables
- Works on any Windows machine

---

## Issue 3: Hidden Submodules Not Collected

### Problem

Specific submodules were not being included in the bundle:
- `transformers.models.auto`
- `transformers.utils`
- `huggingface_hub.file_download`
- `langchain_community.embeddings`
- Etc.

**Root Cause:**
- PyInstaller only includes modules that are explicitly imported
- Dynamic imports via `importlib` are missed
- Entry points (setuptools) aren't analyzed

### Solution Applied

**In `LocalAIAgent_FIXED.spec`:**
Explicitly list all required submodules:
```python
hiddenimports=[
    # transformers
    'transformers',
    'transformers.models',
    'transformers.models.auto',
    'transformers.models.auto.configuration_auto',
    'transformers.models.auto.modeling_auto',
    'transformers.models.auto.tokenization_auto',
    'transformers.models.bert',
    'transformers.models.roberta',
    'transformers.models.distilbert',
    'transformers.utils',
    'transformers.utils.generic',
    'transformers.utils.hub',
    # ... etc for all major packages
]
```

**Why this works:**
- Ensures all dynamically-loaded submodules are pre-collected
- PyInstaller scans bytecode and includes them
- No runtime import errors

---

## Issue 4: C Extension Wrappers Missing

### Problem

Packages that wrap C/Rust libraries had import errors:
- `tokenizers` (Rust tokenizer bindings)
- `safetensors` (C tensor serialization)

**Root Cause:**
- These are binary packages with C/Rust extensions
- PyInstaller sometimes misses the `_module.so` / `_module.pyd` files
- DLL dependencies may not be bundled

### Solution Applied

**In `LocalAIAgent_FIXED.spec`:**
```python
hiddenimports=[
    'tokenizers',
    'tokenizers.tokenizers',
    'tokenizers.implementations',
    'tokenizers.processors',
    # ... all submodules
    'safetensors',
    'safetensors.torch',
    'safetensors.numpy',
]
```

**Why this works:**
- Explicit listing forces PyInstaller to analyze and include all binary files
- Scans for `.pyd` and `.dll` files in the package
- Resolves runtime linking issues

---

## Issue 5: Data Files Not Bundled

### Problem

Configuration files and data files were missing from the executable:
- `configs/` (settings)
- `data/` (default data files)
- UI templates and static files

**Solution Applied**

**In `LocalAIAgent_FIXED.spec`:**
```python
datas_files = []
local_dirs = [
    ('configs', 'configs'),
    ('core', 'core'),
    ('services', 'services'),
    ('agents', 'agents'),
    # ... etc
]
for src, dst in local_dirs:
    src_path = os.path.join(project_root, src)
    if os.path.isdir(src_path):
        a.datas.append((src_path, dst))
```

**Why this works:**
- Data files are copied into the bundle with correct relative paths
- `_MEIPASS` (PyInstaller temp directory) handling is automatic
- Code can use relative imports to find files

---

## Complete Hidden Imports List (Fixed .spec)

### By Category

**1. Packaging & Version Management**
```python
'packaging',
'packaging.version',
'packaging.specifiers',
'packaging.markers',
'packaging.requirements',
'packaging.tags',
```

**2. Transformers & LLM (Large)**
```python
'transformers',
'transformers.models',
'transformers.models.auto',
'transformers.utils',
'tokenizers',
'safetensors',
'torch',
```

**3. Embeddings (HuggingFace)**
```python
'sentence_transformers',
'huggingface_hub',
'huggingface_hub.file_download',
'huggingface_hub.utils',
```

**4. RAG & LangChain**
```python
'langchain',
'langchain_core',
'langchain_community',
'langchain_community.embeddings',
'langchain_community.vectorstores.chroma',
'langchain_text_splitters',
```

**5. Vector Store**
```python
'chromadb',
'chromadb.api',
'chromadb.db',
```

**6. Data Processing**
```python
'pandas',
'numpy',
'scipy',
'PIL',
```

**7. OCR & PDF**
```python
'pdf2image',
'pytesseract',
'pypdf',
```

**8. Email & Google APIs**
```python
'google.auth',
'google.oauth2',
'googleapiclient',
'google_auth_oauthlib',
'imapclient',
```

**9. File Watching & System**
```python
'watchdog',
'watchdog.observers',
'pywin32',
'win32con',
'win32api',
```

**10. Utilities**
```python
'pydantic',
'pydantic_settings',
'requests',
'click',
'dateparser',
```

---

## Runtime Environment Checks

### Created: `core/dependency_checker.py`

Runs on startup to verify:

1. **Python packages installed:**
   - packaging
   - transformers
   - torch
   - sentence_transformers
   - chromadb
   - langchain

2. **Submodules available:**
   - packaging.version
   - transformers.models.auto
   - tokenizers
   - etc.

3. **System binaries present:**
   - Tesseract-OCR
   - Poppler (pdf2image)
   - Ollama daemon

4. **Data directories accessible:**
   - `data/`
   - `data/vector_store/`
   - `data/documents/`

5. **Environment variables set:**
   - TESSERACT_CMD (optional)
   - WINDOWS_DOCS_PATH (optional)
   - LOG_LEVEL (optional)

### Output Example

```
[INFO] Checking runtime dependencies...
✓ Python package 'packaging' available
✓ Python package 'transformers' available
✓ Submodule 'transformers.models.auto' available
✓ Tesseract found: C:\Program Files\Tesseract-OCR\tesseract.exe
[WARNING] Poppler not found on system PATH
[INFO] Ollama daemon is running
✓ All dependency checks passed!
```

---

## Build Process Optimization

### What Gets Included in .exe

**Size breakdown (typical):**
- PyInstaller bootloader: ~1 MB
- Python standard library + runtime: ~50 MB
- NumPy: ~200 MB
- PyTorch: ~600 MB
- Transformers: ~400 MB
- Other packages: ~200 MB
- **Total: ~1.5-2.0 GB**

### Recommendations

1. **Use `--onedir` for deployment:**
   ```powershell
   pyinstaller --clean --onedir LocalAIAgent_FIXED.spec
   ```
   - Results in folder with exe + supporting DLLs
   - Faster startup than `--onefile`
   - Easier to update without rebuilding

2. **Pre-download embeddings:**
   ```python
   from sentence_transformers import SentenceTransformer
   model = SentenceTransformer("all-MiniLM-L6-v2")
   # Saves to ~/.cache/huggingface/
   ```
   - Copy cache to target machine
   - Saves 30+ seconds on first run

3. **Build on same OS as deployment:**
   - Built on Windows → deploy to Windows
   - DLL dependencies must match

---

## Troubleshooting: Build Issues

### "ValueError: entry point XXX not found"

**Cause:** PyInstaller trying to analyze a package entry point that doesn't exist

**Solution:**
```bash
pyinstaller --clean --ignore-hidden-import=<package> LocalAIAgent_FIXED.spec
```

### "AttributeError: module 'X' has no attribute 'Y'"

**Cause:** A submodule wasn't collected

**Solution:**
1. Add to `hiddenimports` in .spec
2. Rebuild:
   ```powershell
   pyinstaller --clean LocalAIAgent_FIXED.spec
   ```

### Build takes >30 minutes

**Cause:** PyInstaller analyzing large packages

**Solution:**
- Use SSD (faster I/O)
- Close other programs (CPU/RAM)
- Pre-build once, then reuse `.spec` file

---

## Testing the Build

### Test 1: Verify imports
```powershell
# Run with built executable
dist\LocalAIAgent.exe -c "import packaging; import transformers; print('OK')"
```

### Test 2: Verify OCR setup
```powershell
# Check if Tesseract is found
dist\LocalAIAgent.exe -c "from services.ocr_service import ocr_service; print(f'OCR: {ocr_service.is_available}')"
```

### Test 3: Verify data persistence
```powershell
# Check if data directory created correctly
dist\LocalAIAgent.exe
# Type: "Forget me"
# Check: data/memory.json should be created
```

---

## Summary of Changes

| Component | Before | After | Benefit |
|-----------|--------|-------|---------|
| .spec file | Minimal imports | Complete list (60+ items) | No missing dependencies |
| Embedding loader | Crash on error | Pre-check + graceful fail | Clear error messages |
| OCR handling | Hard-coded path | Dynamic detection + fallback | Works on any machine |
| Startup | No checks | Dependency validation | Detects issues early |
| Tesseract path | Fixed C: path | Env var + multiple paths | Flexible installation |
| Error handling | Exception crash | Logged + continue | Graceful degradation |
| Data files | Manual copy | Included in .spec | No manual steps |

---

## Key Takeaways

1. **Use the FIXED.spec file** - it has 20+ years of collective PyInstaller wisdom baked in
2. **Packaging module is critical** - must be explicitly included
3. **System binaries can't be bundled** - Tesseract/Poppler/Ollama installed separately
4. **Graceful fallback is key** - missing optional deps shouldn't crash app
5. **Dependency checks on startup** - catch issues before user notices
6. **Data persists in `data/` folder** - separate from executable

---

## References

- PyInstaller docs: https://pyinstaller.org/
- Transformers docs: https://huggingface.co/docs/transformers/
- Tesseract: https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe
- Poppler: https://github.com/oschwartz10612/poppler-windows/releases
