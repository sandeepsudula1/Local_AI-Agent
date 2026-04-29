# PyInstaller Quick Start - Build & Deploy in 5 Minutes

## TL;DR

```powershell
# 1. Activate environment
.\venv311\Scripts\Activate.ps1

# 2. Build executable
pyinstaller --clean LocalAIAgent_FIXED.spec

# 3. Test
.\dist\LocalAIAgent.exe

# 4. (Optional) Install system dependencies
# - Tesseract-OCR
# - Poppler
# - Ollama

# Done! Ship dist\LocalAIAgent.exe to users
```

---

## Prerequisites

- [ ] Python 3.11
- [ ] PyInstaller 6.1+ (`pip install pyinstaller`)
- [ ] All requirements installed (`pip install -r requirements.txt`)

---

## Build Steps

### Step 1: Activate Virtual Environment

```powershell
.\venv311\Scripts\Activate.ps1
```

### Step 2: Clean Build

```powershell
pyinstaller --clean LocalAIAgent_FIXED.spec
```

**Expected output:**
```
121 INFO: Building because main.py changed
...
195 INFO: Appending archive to EXE
196 INFO: Building EXE from EXE-00.toc completed successfully.
```

**Time:** 5-15 minutes depending on system

### Step 3: Locate Executable

```powershell
# For .exe:
ls dist\LocalAIAgent.exe

# For folder (if using --onedir):
ls dist\LocalAIAgent\LocalAIAgent.exe
```

### Step 4: Test on Local Machine

```powershell
.\dist\LocalAIAgent.exe
```

**Expected output:**
```
[INFO] Running mode: Frozen executable (.exe)
[INFO] Checking runtime dependencies...
✓ All dependency checks passed!

Smart AI Multi-Agent System Ready.
```

Type `exit` to quit

---

## Install System Dependencies (on Deployment Machine)

### Option 1: All Features (Recommended)

```powershell
# 1. Tesseract-OCR
# Download: https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe
# Run installer, use default path

# 2. Poppler
# Download: https://github.com/oschwartz10612/poppler-windows/releases
# Extract, add bin\ to PATH

# 3. Ollama
# Download: https://ollama.ai
# Install, run: ollama serve
```

### Option 2: Minimal (Document Search Only)

Just copy `LocalAIAgent.exe` and run - basic features work without dependencies

### Option 3: LLM Only

Install just Ollama for local LLM inference:
```powershell
# Download and install Ollama
# Then: ollama pull mistral
```

---

## Deployment Options

### Option A: Copy Single Executable

```powershell
# For users
copy .\dist\LocalAIAgent.exe C:\Users\YourName\Desktop\
# Or copy to network share
```

**Pros:** Simple, single file
**Cons:** Slower startup (first time), larger file

### Option B: Copy Directory (Recommended)

```powershell
# Use --onedir build (faster startup)
pyinstaller --clean --onedir LocalAIAgent_FIXED.spec

# Copy entire folder
copy .\dist\LocalAIAgent C:\Program Files\LocalAIAgent\
```

**Pros:** Faster startup, easier to update
**Cons:** Larger total size (multiple files)

### Option C: Create Installer

```powershell
# Edit LocalAIAgent_Installer.iss to point to dist\LocalAIAgent\

# Download Inno Setup (free)
# Then build installer:
iscc LocalAIAgent_Installer.iss

# Output: LocalAIAgent_Setup.exe
```

**Pros:** Professional, Windows installer, automatic updates
**Cons:** More complex

---

## Verify Deployment

After deploying on target machine:

```powershell
# 1. Run the app
LocalAIAgent.exe

# 2. Check dependency warnings
# Look for [WARNING] messages about missing Tesseract/Poppler/Ollama

# 3. Test each feature
# - Document search: "How many employees?"
# - Image OCR: Upload image file
# - Reminders: "Remind me at 15:30 to call Alice"

# 4. Check data folder created
dir data\
# Should see: vector_store, logs, memory.json, etc.
```

---

## Troubleshooting (2-Minute Fixes)

### Problem: "No module named 'packaging'"

```powershell
# Rebuild with FIXED.spec (not the old one)
pyinstaller --clean LocalAIAgent_FIXED.spec
```

### Problem: OCR not working

```powershell
# Install Tesseract
# https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe

# Verify:
"C:\Program Files\Tesseract-OCR\tesseract.exe" --version
```

### Problem: "pytesseract requires Tesseract-OCR installed"

Already covered above - app still works, just OCR disabled

### Problem: High memory usage

- Normal: 500 MB - 1.2 GB (depends on documents loaded)
- If >2 GB consistently: reduce document chunk size in settings

---

## What to Tell Users

**Installation instructions to send to users:**

> 1. Download LocalAIAgent.exe
> 2. Run it (Windows Defender may warn, it's safe)
> 3. First startup takes 30-60 seconds (downloading models)
> 4. Subsequent starts take 5 seconds
> 5. (Optional) For better document handling:
>    - Install Tesseract-OCR: https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe
>    - Install Poppler: https://github.com/oschwartz10612/poppler-windows/releases
>    - Install Ollama for LLM: https://ollama.ai

---

## Build Variants

### Standard (Recommended)

```powershell
pyinstaller --clean LocalAIAgent_FIXED.spec
# Output: dist\LocalAIAgent.exe (~1.8 GB)
```

### Optimized for Distribution

```powershell
pyinstaller --clean --onedir LocalAIAgent_FIXED.spec
# Output: dist\LocalAIAgent\ folder (~1.8 GB total)
# Benefits: Faster startup, easier to update
```

### Single File (Slower Startup)

```powershell
# Edit LocalAIAgent_FIXED.spec: Add onefile=True
pyinstaller --clean LocalAIAgent_FIXED.spec
# Output: dist\LocalAIAgent.exe (~2.2 GB, single file)
```

### Debug Mode

```powershell
pyinstaller --debug=all LocalAIAgent_FIXED.spec
# Keeps temporary files for inspection
```

---

## Performance Expectations

| Metric | Time |
|--------|------|
| Build time | 5-15 min |
| First run (fresh system) | 30-60 sec |
| Subsequent runs | 5-10 sec |
| Document indexing (1000 docs) | 2-5 min |
| Vector store search | <100 ms |
| OCR on image | 1-5 sec |
| LLM inference (Ollama) | 5-30 sec |

---

## Files Reference

| File | Purpose |
|------|---------|
| `LocalAIAgent_FIXED.spec` | ← Use this .spec file |
| `main.py` | Entry point |
| `core/dependency_checker.py` | NEW: Runtime validation |
| `core/runtime_paths.py` | NEW: Path detection |
| `services/ocr_service.py` | NEW: Tesseract wrapper |
| `engines/embedding_engine.py` | UPDATED: Error handling |
| `agents/knowledge/retrieval_agent.py` | UPDATED: Uses OCR service |
| `PYINSTALLER_DEPLOYMENT_GUIDE.md` | Detailed guide |
| `PYINSTALLER_TECHNICAL_REFERENCE.md` | Technical details |

---

## Next Steps

1. **Build now:**
   ```powershell
   pyinstaller --clean LocalAIAgent_FIXED.spec
   ```

2. **Test locally:**
   ```powershell
   .\dist\LocalAIAgent.exe
   ```

3. **Deploy to users:**
   - Copy `dist\LocalAIAgent.exe` or `dist\LocalAIAgent\` folder
   - Share deployment guide above

4. **Get feedback:**
   - Any missing features?
   - Dependency issues?
   - Performance problems?

---

## Quick Diagnosis

**If something goes wrong:**

1. Check logs:
   ```powershell
   cat data\logs\app.log
   ```

2. Run dependency check:
   ```powershell
   .\dist\LocalAIAgent.exe
   # Look for [WARNING] or [ERROR] messages
   ```

3. Verify system binaries:
   ```powershell
   # Check Tesseract
   "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
   
   # Check Poppler
   pdftoppm --version
   
   # Check Ollama
   ollama list
   ```

4. Check disk space (need 2-3 GB for models on first run)

---

## Support

- **Detailed docs:** See `PYINSTALLER_DEPLOYMENT_GUIDE.md`
- **Technical details:** See `PYINSTALLER_TECHNICAL_REFERENCE.md`
- **Issues:** Check app logs in `data/logs/`

---

**You're ready to build and deploy!**

Questions? Check the detailed guides above.
