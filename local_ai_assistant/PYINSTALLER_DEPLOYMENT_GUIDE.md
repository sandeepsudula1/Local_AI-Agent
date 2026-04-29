# PyInstaller Deployment Guide - Local AI Assistant

## Overview

This guide covers building and deploying the Local AI Assistant as a Windows executable using PyInstaller, with complete handling of:
- **"packaging" module issues** (embedding models)
- **Tesseract-OCR** (OCR for documents/images)
- **Other hidden dependencies** (transformers, huggingface_hub, langchain)

---

## Part 1: Prerequisites

### System Requirements

**Windows 10/11 (64-bit)**
- Python 3.11 (must match the venv used for development)
- PyInstaller 6.1+
- 2GB+ disk space for .exe (transformers + embeddings are large)

### Pre-Build Checklist

1. **Verify Python version** (must be 3.11):
   ```powershell
   python --version
   ```

2. **Activate your venv**:
   ```powershell
   .\venv311\Scripts\Activate.ps1
   ```

3. **Install PyInstaller** (if not already installed):
   ```powershell
   pip install pyinstaller==6.1.0
   ```

4. **Verify all dependencies are installed**:
   ```powershell
   pip install -r requirements.txt
   ```

---

## Part 2: Building the Executable

### Step 1: Use the Fixed .spec File

The new spec file `LocalAIAgent_FIXED.spec` includes ALL required hidden imports:

```powershell
# Clean build (removes old artifacts)
pyinstaller --clean LocalAIAgent_FIXED.spec
```

### Step 2: Monitor Build Progress

The build will take 5-15 minutes depending on your system:

```
1. Analyzing packages...  (5-10 min)
2. Collecting data files... (1-2 min)
3. Building bootloader... (1-2 min)
4. Packaging...           (1 min)
```

**Expected output:**
```
Successfully built LocalAIAgent
191 INFO: Appending archive to EXE
192 INFO: Building EXE from EXE-00.toc completed successfully.
```

### Step 3: Locate the Built Executable

After successful build:

```
dist/
└── LocalAIAgent.exe   ← Your executable
    
OR (if using --onedir):

dist/
└── LocalAIAgent/
    ├── LocalAIAgent.exe
    ├── _internal/       (all bundled packages)
    └── data/            (config files)
```

### Build Recommendations

**For initial testing (faster build):**
```powershell
pyinstaller --clean LocalAIAgent_FIXED.spec
```

**For production/distribution (better compatibility):**
```powershell
pyinstaller --clean --onedir LocalAIAgent_FIXED.spec
```

**For single-file distribution (larger but simpler):**
```powershell
pyinstaller --clean --onefile LocalAIAgent_FIXED.spec
```

---

## Part 3: System Dependencies (Before Running .exe)

### 1. Tesseract-OCR (Optional but Recommended)

**Required for:** Image/PDF OCR functionality

**Installation steps:**

1. Download: https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe
2. Run installer (use default path: `C:\Program Files\Tesseract-OCR`)
3. Verify installation:
   ```powershell
   C:\Program Files\Tesseract-OCR\tesseract.exe --version
   ```

**Alternative: Add to PATH environment variable:**

If installed elsewhere, set the path:

```powershell
$env:TESSERACT_CMD = "C:\path\to\tesseract.exe"
[Environment]::SetEnvironmentVariable("TESSERACT_CMD", "C:\path\to\tesseract.exe", "User")
```

### 2. Poppler (Optional but Recommended)

**Required for:** PDF-to-image conversion (needed for OCR on PDFs)

**Installation steps:**

1. Download: https://github.com/oschwartz10612/poppler-windows/releases
2. Extract to a folder (e.g., `C:\tools\poppler-24.02\bin`)
3. Add to system PATH:
   - Open Settings → Environment Variables
   - Add `C:\tools\poppler-24.02\bin` to PATH
4. Verify:
   ```powershell
   pdftoppm --version
   ```

### 3. Ollama (Optional - for LLM inference)

**Required for:** Running local LLM models

**Installation steps:**

1. Download: https://ollama.ai
2. Install and start the Ollama service:
   ```powershell
   ollama serve
   ```
3. In another terminal, pull a model:
   ```powershell
   ollama pull mistral
   ollama list
   ```

**Note:** Ollama requires ~4GB of free RAM and substantial disk space per model.

---

## Part 4: Running the Executable

### First Run

```powershell
cd dist
.\LocalAIAgent.exe
```

### Expected Startup Output

```
[INFO] Running mode: Frozen executable (.exe)
[INFO] Data directory: C:\...\dist\data
[INFO] Tesseract: Found
[INFO] Poppler: Found
[INFO] Ollama: Found

[INFO] Checking runtime dependencies...
✓ All dependency checks passed!

[INFO] Loading documents…
Loaded 42 document chunk(s).

[INFO] Starting vector store service…
[INFO] Starting reminder service…

Smart AI Multi-Agent System Ready.

Examples:
  - Which is better, Python or Java?
  - Compare 'Node.js' vs 'Deno'
  - Remind me at 15:30 to call Alice
```

---

## Part 5: Deployment to Another Machine

### Scenario: Run on a Clean Windows 10 Machine

1. **Copy the .exe to target machine:**
   ```
   C:\Users\<user>\AppData\Local\LocalAIAgent\
   ```

2. **Install system dependencies** (if needed):
   - Tesseract-OCR (for document processing)
   - Poppler (for PDF conversion)
   - Ollama (for LLM inference)

3. **Run the executable:**
   ```powershell
   LocalAIAgent.exe
   ```

4. **Data persistence:**
   - `data/` folder will be created next to the .exe
   - Contains:
     - `vector_store/` - ChromaDB embeddings cache
     - `credentials.json` - Gmail OAuth token
     - `memory.json` - Conversation history
     - Logs and other caches

### Create an Installer

Use Inno Setup (already configured):

```powershell
# Edit LocalAIAgent_Installer.iss to point to dist\LocalAIAgent.exe

# Build installer
iscc LocalAIAgent_Installer.iss

# Output: LocalAIAgent_Setup.exe
```

---

## Part 6: Troubleshooting

### Issue 1: "No module named 'packaging'"

**Symptom:** Crash when loading embeddings
```
ModuleNotFoundError: No module named 'packaging'
```

**Solution:**
1. Verify you're using `LocalAIAgent_FIXED.spec` (not the old .spec)
2. Rebuild with:
   ```powershell
   pyinstaller --clean LocalAIAgent_FIXED.spec
   ```
3. Check the build log for `packaging` in hiddenimports

**Root cause:** PyInstaller missed the `packaging` module (indirect dependency of transformers)

---

### Issue 2: "pytesseract requires Tesseract-OCR installed"

**Symptom:** OCR fails silently or with error message

**Solution:**
1. Install Tesseract:
   ```powershell
   # Download and run installer
   # https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe
   ```

2. Verify installation:
   ```powershell
   "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
   ```

3. If installed elsewhere, set environment variable:
   ```powershell
   $env:TESSERACT_CMD = "C:\path\to\tesseract.exe"
   ```

**Graceful handling:** The app will log a warning but continue working (OCR just won't be available)

---

### Issue 3: "PDFInfoNotInstalledError" - Poppler not found

**Symptom:** PDF to image conversion fails

**Solution:**
1. Install Poppler:
   ```powershell
   # Download from
   # https://github.com/oschwartz10612/poppler-windows/releases
   ```

2. Add `bin/` folder to system PATH:
   - Settings → Environment Variables → PATH → Add
   - `C:\path\to\poppler-24.02\bin`

3. Verify:
   ```powershell
   pdftoppm --version
   ```

**Graceful handling:** The app will use fallback text extraction, skipping OCR

---

### Issue 4: "Cannot load embedding model"

**Symptom:** Vector store initialization fails

**Possible causes:**

1. **No internet connection:**
   - Models are downloaded on first use
   - Pre-download on connected machine:
     ```python
     from sentence_transformers import SentenceTransformer
     model = SentenceTransformer("all-MiniLM-L6-v2")
     # This downloads ~90 MB to cache
     ```

2. **Missing transformers dependencies:**
   - Verify: `packaging`, `tokenizers`, `safetensors` are included
   - Rebuild .spec if needed

3. **CUDA/GPU issues:**
   - Set `EMBEDDING_DEVICE=cpu` if GPU driver missing
   - CPU will be slower but always works

---

### Issue 5: Startup Takes Too Long

**Normal times:**
- First run: 30-60 sec (downloads embedding models)
- Subsequent runs: 5-10 sec (models cached)

**Optimization:**
1. Pre-download models on connected machine
2. Copy `~/.cache/huggingface/` to target machine
3. Set `HF_HOME` environment variable to cache location

---

### Issue 6: High Memory Usage / Crashes

**Typical memory usage:**
- Base: 200-400 MB
- With embeddings loaded: 800 MB - 1.2 GB
- With Ollama LLM: 2-4 GB+ (depends on model)

**Solutions:**
1. Reduce document chunk size in settings
2. Run on machine with 4+ GB RAM
3. Use lighter embedding model: `all-MiniLM-L6-v2` (recommended)

---

## Part 7: Verification Checklist

After deployment, verify all systems working:

```powershell
# 1. App starts without errors
LocalAIAgent.exe

# 2. Check startup logs
cat data/logs/app.log

# 3. Test document search (once loaded)
# Type: "How many employees in 2024?"

# 4. Test OCR (if Tesseract installed)
# Upload image or PDF with text

# 5. Test Ollama integration (if installed)
# Type: "What is machine learning?"

# 6. Test Gmail integration
# Grant OAuth permissions when prompted

# 7. Check data directory created
dir data/
```

**Expected data directory structure:**
```
data/
├── credentials.json         (Gmail OAuth)
├── gmail_token.json        (OAuth token)
├── memory.json             (Conversation history)
├── reminders.json          (Scheduled reminders)
├── documents/              (User documents)
├── vector_store/           (ChromaDB)
├── vector_store_win_docs/  (Windows docs index)
└── logs/                   (Application logs)
```

---

## Part 8: Performance Tuning

### For Production Deployment

1. **Use --onedir mode:**
   ```powershell
   pyinstaller --clean --onedir LocalAIAgent_FIXED.spec
   ```
   - Faster startup (files not packed in .exe)
   - Easier to update individual files
   - Slightly larger total size

2. **Pre-cache embeddings:**
   - Pre-download on connected machine
   - Copy cache to deployment machine
   - Saves 30sec on first startup

3. **Disable verbose logging:**
   - Set `LOG_LEVEL=warning` in settings.ini
   - Reduces disk I/O

4. **Separate data directory:**
   - For multi-user deployments, put `data/` on shared network drive
   - Allows centralized document management

---

## Part 9: Common .spec File Issues

### Issue: "AttributeError: module has no attribute"

This typically means a submodule wasn't collected.

**Solution:**
1. Add the module to `hiddenimports` in .spec
2. Rebuild:
   ```powershell
   pyinstaller --clean LocalAIAgent_FIXED.spec
   ```

### Issue: Executable Too Large (>2GB)

**Solution:**
1. Use `--onedir` instead of `--onefile`
2. Remove unnecessary data files
3. Consider using Conda-Pack for distribution

---

## Part 10: Quick Reference - Build Commands

```powershell
# Clean build (recommended)
pyinstaller --clean LocalAIAgent_FIXED.spec

# Debug mode (keep temporary files)
pyinstaller --debug=all LocalAIAgent_FIXED.spec

# Optimized for size
pyinstaller --optimize=2 LocalAIAgent_FIXED.spec

# Optimized for one-directory distribution
pyinstaller --clean --onedir LocalAIAgent_FIXED.spec

# With analysis report
pyinstaller --clean --analyze LocalAIAgent_FIXED.spec
pyinstaller --clean LocalAIAgent_FIXED.spec
```

---

## Support & Debugging

### Get Detailed Build Information

```powershell
pyinstaller --clean LocalAIAgent_FIXED.spec 2>&1 | Tee-Object build.log
```

### Check What's Bundled

```powershell
# View spec file
Get-Content LocalAIAgent_FIXED.spec | Select-String "hiddenimports"

# List files in executable (requires Windows tools)
# Or extract dist\ folder and inspect
```

### Test Imports in Frozen Environment

After building, verify key packages load:

```powershell
# Create test script: test_imports.py
import sys
try:
    import packaging
    print("✓ packaging")
except:
    print("✗ packaging")

try:
    import transformers
    print("✓ transformers")
except:
    print("✗ transformers")

# Run with exe
dist\LocalAIAgent.exe test_imports.py
```

---

## Final Notes

- **Always use the FIXED.spec file** - it has all the necessary hidden imports
- **Dependency checker runs on startup** - you'll see warnings for missing optional dependencies
- **Data persists separately** - safe to update .exe without losing user data
- **Most failures are graceful** - missing Tesseract = no OCR, but app still works

For support, check:
1. `data/logs/app.log` (detailed error logs)
2. Application startup output (dependency warnings)
3. Troubleshooting section above
