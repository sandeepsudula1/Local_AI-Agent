# Windows Deployment Plan: Local AI Assistant

**Document Version:** 1.0  
**Target OS:** Windows 10/11  
**Target Python:** 3.11+  
**PyInstaller:** 6.1+  
**Inno Setup:** 6.2+  
**Last Updated:** April 2026

---

## Executive Summary

The **Local AI Assistant** is a Python-based intelligent agent system that:
- Processes documents (PDF, DOCX, images) with OCR fallback
- Manages email drafts and retrieval via Gmail API
- Schedules reminders with Windows notifications
- Indexes Windows Documents folder with semantic search
- Runs local LLM inference via Ollama
- Provides CLI and Streamlit UI

This deployment plan converts the development environment into a **standalone Windows executable** with an automated installer.

---

## 1. PROJECT ANALYSIS

### 1.1 Project Type
- **Language:** Python 3.11
- **Primary Entry Points:**
  - `main.py` → CLI interactive mode (recommended for .exe)
  - `streamlit_app.py` → Web UI (optional, requires `streamlit run`)
  - `smart_agent.py` → Legacy entry point (not recommended)

### 1.2 Architecture Overview

```
Local AI Assistant
├── Core Services (runs background threads)
│   ├── DocumentService → Load & parse documents
│   ├── VectorStoreService → ChromaDB embeddings (background)
│   ├── ReminderService → Schedule Windows notifications (background)
│   ├── EmailPollingService → Gmail sync every 60s (background)
│   ├── DocumentIndexerService → Windows Docs indexing (background)
│   └── FileWatcherService → Monitor file changes (background)
├── Agents (stateless processors)
│   ├── EmailQueryAgent → Search & summarize emails
│   ├── RemoteRetrieval → File Q&A with RAG
│   ├── EmailDraftAgent → Compose email drafts
│   ├── ReminderAgent → Parse & schedule reminders
│   └── 20+ other specialized agents
└── Orchestrator (intent routing)
    └── Routes user input → best-fit agent → services
```

### 1.3 Supported Entry Modes
1. **Interactive CLI** ← **Recommended for .exe**
   - `main.py` runs REPL loop
   - No UI dependencies
   - Minimal startup time

2. **Web UI (Streamlit)**
   - `streamlit_app.py`
   - Requires: Streamlit bundled with exe
   - More memory overhead

3. **MCP Server Mode** (Model Context Protocol)
   - `main.py --mcp` or `--mcp-sse`
   - For editor/IDE integration

---

## 2. DEPENDENCIES ANALYSIS

### 2.1 Python Packages (via requirements.txt)

**Core Framework (18 packages)**
- LLM/Embeddings: `ollama`, `sentence-transformers`, `transformers`, `huggingface_hub`, `torch`
- Vector Store: `chromadb`
- LangChain: `langchain`, `langchain-community`, `langchain-core`, `langchain-text-splitters`
- Document Processing: `pandas`, `numpy`, `scipy`, `Pillow`, `pytesseract`, `pdf2image`, `pypdf`, `python-docx`, `python-pptx`, `openpyxl`
- Email: `IMAPClient`, `google-auth`, `google-auth-oauthlib`, `google-api-python-client`
- Utilities: `pydantic`, `requests`, `click`, `dateparser`, `tqdm`
- UI: `streamlit` (optional)
- Notifications: `plyer`, `pywin32`, `win10toast`

**Total packages:** 65 (all listed in requirements.txt)
**Total size:** ~2.5 GB with PyTorch/transformers

### 2.2 External System Dependencies (CRITICAL)

| Dependency | Purpose | Location | Status | Bundling |
|-----------|---------|----------|--------|----------|
| **Tesseract-OCR** | PDF/image text extraction | `C:\Program Files\Tesseract-OCR\tesseract.exe` | OPTIONAL | ❌ Install separately |
| **Poppler** | PDF → image conversion (OCR fallback) | System PATH | OPTIONAL | ❌ Install separately |
| **Ollama** | Local LLM backend (llama3) | `http://localhost:11434` | REQUIRED | ❌ Run separately |
| **Chrome/Edge/VS Code** | System task execution | Standard locations | OPTIONAL | ⚪ Auto-detected |

### 2.3 Runtime Models (Downloaded on First Run)

| Model | Source | Destination | Size | Used By |
|-------|--------|-------------|------|---------|
| `sentence-transformers/all-MiniLM-L6-v2` | HuggingFace Hub | `~/.cache/huggingface/` | ~90 MB | VectorStore embeddings |
| `ollama` models (llama3, etc.) | Ollama daemon | Ollama internal | Variable | LLM inference |
| `faster-whisper` (optional) | HuggingFace | `~/.cache/` | ~141 MB | Audio transcription |

**Total first-run download:** ~300+ MB (models + embeddings)

### 2.4 API Keys & Credentials (User-Provided)

| Credential | File | Required? | Obtained From |
|-----------|------|-----------|---------------|
| **Google OAuth Client ID** | `data/credentials.json` | Required for email | Google Cloud Console |
| **Gmail OAuth Token** | `data/gmail_token.json` | Auto-created on login | OAuth flow (HTTPS redirect) |
| **SMTP Credentials** | `.env` vars: `EMAIL_USER`, `EMAIL_PASS` | Optional | Email provider |
| **API Keys** | None required (offline-first design) | ✅ | N/A |

---

## 3. RESOURCE FILES & DATA

### 3.1 Configuration Files

```
PROJECT_ROOT/
├── .env (optional, runtime config)
├── configs/
│   └── settings.py (hardcoded defaults, can be overridden)
└── data/
    ├── credentials.json (USER SETUP REQUIRED)
    ├── gmail_token.json (auto-created)
    ├── memory.json (auto-created)
    ├── reminders.json (auto-created)
    ├── drafts.json (auto-created)
    ├── email_cache.json (auto-created)
    ├── granted_folders.json (auto-created)
    ├── file_index.db (SQLite, auto-created)
    └── win_docs_index_state.json (auto-created)
```

### 3.2 Vector Store Databases (Size Grows with Docs)

```
data/
├── vector_store/            # Project documents
├── vector_store_v2/         # (deprecated?)
├── vector_store_audio/      # Audio transcription
└── vector_store_win_docs/   # Windows Documents folder
```

### 3.3 User Data Directories

```
data/
├── documents/               # User uploads documents here
├── audio/                   # Audio files for transcription
└── logs/                    # Application logs
```

### 3.4 WAS: Hardcoded Paths (MUST FIX FOR DEPLOYMENT)

| File | Line | Path | Fix |
|------|------|------|-----|
| `retrieval_agent.py` | 1196, 1280, 1613 | `C:\Program Files\Tesseract-OCR\tesseract.exe` | ✅ Already configurable via `settings.tesseract_cmd` |
| `configs/settings.py` | 255 | `C:\AI_Test_Documents` | ✅ Configurable via `WINDOWS_DOCS_PATH` env var |
| `system_agent.py` | 32-33 | Chrome/Edge paths | ✅ Auto-detected via `shutil.which()` |

**Status:** ✅ **EXCELLENT** — All paths are already runtime-configurable

---

## 4. ENVIRONMENT VARIABLES (Deployment Configuration)

### 4.1 Critical Variables (Must Provide)

| Variable | Default | Suggestion for Deployment | Example |
|----------|---------|---------------------------|---------|
| `TESSERACT_CMD` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Same (install Tesseract to standard location) | `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| `WINDOWS_DOCS_PATH` | `C:\AI_Test_Documents` | Point to actual folder user wants indexed | `C:\AI_Test_Documents` or `%USERPROFILE%\Documents` |
| `USER_NAME` | `Sandeep` | Prompt user during install | `%USERNAME%` (Windows system variable) |

### 4.2 Optional Email Configuration

| Variable | Default | For Email Support |
|----------|---------|-------------------|
| `EMAIL_HOST` | `` | `smtp.gmail.com` or `smtp.live.com` |
| `EMAIL_PORT` | 587 | `587` (TLS) or `25`/`465` (SSL) |
| `EMAIL_USER` | `` | User's email address |
| `EMAIL_PASS` | `` | Email password or app-specific password |
| `EMAIL_FROM` | `` | Sender display name |

### 4.3 Optional Model Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MODEL_NAME` | `llama3` | Ollama model to use |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `EMBEDDING_DEVICE` | `cpu` | `cpu` or `cuda` (GPU) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### 4.4 Recommended Inno Setup Integration

Create `.env` file **during installation**:
- Use Inno Setup to collect configuration via installer wizard
- Write to `{app}\.env` with user-provided values
- Mark as user-editable after install (notepad shortcut)

---

## 5. HARDCODED PATHS - ASSESSMENT & FIXES

### 5.1 Path Analysis Result: ✅ EXCELLENT

| Category | Finding | Risk | Fix |
|----------|---------|------|-----|
| **Tesseract** | Hardcoded in 3 places, but settable via env | ⚠️ Medium | Already handled |
| **Windows Docs** | Hardcoded to `C:\AI_Test_Documents` | ⚠️ Medium | Configurable via env |
| **DATA_DIR** | Smart detection for PyInstaller | ✅ Low | Dynamic at runtime |
| **PROJECT_ROOT** | Smart detection for PyInstaller | ✅ Low | Dynamic at runtime |
| **Browser paths** | Auto-detected via `shutil.which()` | ✅ Low | Graceful fallback |
| **Google creds** | Loaded from `data/credentials.json` | ⚠️ Medium | User provides at install |

### 5.2 Key Design: PyInstaller-Aware Paths

**In `configs/settings.py`:**
```python
if getattr(sys, "frozen", False):
    # Running as PyInstaller .exe
    PROJECT_ROOT = Path(sys.executable).parent  # {app}\
    DATA_DIR = %APPDATA%\LocalAIAgent\data      # User-writable
else:
    # Running from source
    PROJECT_ROOT = os.path.dirname(__file__)    # project root
    DATA_DIR = PROJECT_ROOT / "data"
```

**This means:**
- ✅ `.exe` reads config from `{app}\.env`
- ✅ Data stored in `%APPDATA%\LocalAIAgent\data` (never overwritten)
- ✅ Source code unchanged, deployment-ready

---

## 6. PYINSTALLER CONFIGURATION

### 6.1 Recommended Build Approach

**Single-file executable** (simpler deployment, slower startup):
```bash
pyinstaller LocalAIAgent_enhanced.spec --onefile
```

**Benefits:**  
- ✅ Single `.exe` to download/install
- ✅ End-user can't accidentally delete DLLs
- ✅ Windows Defender less suspicious

**Downside:**  
- ⚠️ First startup unpacks to `%TEMP%` (~3-5 seconds)

### 6.2 Generated Spec File Structure

**See: `LocalAIAgent_enhanced.spec`**

Key configurations:
- **Entry point:** `main.py` (not `smart_agent.py`)
- **Console mode:** `console=True` (shows CLI output)
- **Package includes:** All agents, services, tools
- **Hidden imports:** PyWin32, chromadb, transformers, ollama
- **Excluded:** Tests, __pycache__, .git, venv311

### 6.3 Size Estimation

| Component | Size | Notes |
|-----------|------|-------|
| Python runtime | ~50 MB | Included in exe |
| Core packages | ~200 MB | NumPy, transformers, torch (largest) |
| Dependencies | ~100 MB | All other packages |
| Data files | ~5 MB | Minimal (configs, empty), grows with use |
| **Total .exe** | **~350 MB** | Single-file executable |
| **After first run** | **+~300 MB** | Downloaded models in `~/.cache/` |

---

## 7. BUNDLE STRUCTURE FOR DEPLOYMENT

### 7.1 Clean Pre-Build Folder Structure

```
LocalAIAgent/          (deploy folder)
├── LocalAIAgent.exe   (from pyinstaller --onefile)
├── .env               (config file - created by installer)
├── README.txt         (quick start guide)
├── SETUP.txt          (manual configuration steps)
└── data/              (created by installer, or on first run)
    ├── documents/
    ├── audio/
    └── logs/
```

### 7.2 Optional: Multi-File Layout (--onedir)

```
LocalAIAgent/
├── LocalAIAgent.exe
├── _internal/         (DLLs, Python files, resources)
├── .env
├── data/
└── README.txt
```

**Advantage:** Faster startup, easier to debug  
**Disadvantage:** More files to manage

---

## 8. RUNTIME ISSUES & MITIGATIONS

### 8.1 Windows Defender / SmartScreen

**Problem:** `.exe` may be flagged as malicious (PUP, trojan)  
**Cause:** PyInstaller-generated executables lack code signing

**Mitigations:**
1. Add manifest to suppress UAC prompts
2. Code-sign the `.exe` (requires certificate, optional)
3. Test on fresh Windows VM to validate
4. Document that user may need to select "Run anyway"

### 8.2 Missing DLLs at Runtime

**Problem:** `ImportError` or `DLL not found` errors  
**Cause:** PyInstaller missed a hidden import or binary

**Mitigation in Spec File:**
```python
hiddenimports=[
    'pywin32',
    'chromadb',
    'transformers',
    'ollama',
    'pydantic',
    'sentence_transformers',
    'langchain_community',
    # ... see spec file for full list
]
```

### 8.3 PyTorch Initialization Slow

**Problem:** First import of `torch` takes 10+ seconds  
**Cause:** NumPy/PyTorch lazy loading

**Mitigation:** Pre-import large packages before showing splash screen (handled in `main.py`)

### 8.4 Ollama Not Running

**Problem:** "Unable to reach Ollama at localhost:11434"  
**Cause:** Ollama daemon not started

**Mitigation:**
1. Check for Ollama on system PATH
2. Auto-launch Ollama if found
3. Show helpful error message: "Download Ollama from https://ollama.ai"

### 8.5 Tesseract Not Found

**Problem:** PDF OCR fails silently  
**Cause:** Tesseract not installed or wrong path

**Mitigation:**
- ✅ Already handled with try/except + helpful error message
- Fallback to PDF PyPDFLoader (no OCR)
- Suggest installation: "Download Tesseract from..."

### 8.6 Google OAuth Flow in .exe

**Problem:** OAuth redirect from Google redirects to `http://localhost:port`  
**Cause:** Some firewalls block localhost loopback

**Mitigation:**
- Use default OAuth redirect: `http://localhost` (port-less)
- Provide manual token setup: "Copy token from browser, paste here"

### 8.7 First-Run Model Download (No Internet)

**Problem:** ~300 MB of models downloaded on first run  
**Cause:** Offline-first design, models not pre-bundled

**Mitigation:**
- Show progress bar during download
- Cache in `~/.cache/huggingface`
- **Pre-bundle models in Pro installer** (future enhancement)

### 8.8 File Permissions (Program Files)

**Problem:** Cannot write to Program Files on non-admin installs  
**Cause:** Windows write protection

**Mitigation:**
- ✅ Already handled: `DATA_DIR` → `%APPDATA%\LocalAIAgent\data`
- Pre-create subdirectories in `configs/settings.py`
- No write to `{app}\` after startup

### 8.9 Antivirus False Positives

**Problem:** Antivirus blocks `.exe` or quarantines it  
**Cause:** Binary contains Python bytecode scanner misidentifies

**Mitigation:**
1. Test on VirusTotal before release
2. Contact antivirus vendors for code signing review
3. Document exclusion rules for Windows Defender
4. Supply `.asc` signature file if applicable

---

## 9. INNO SETUP INSTALLER

### 9.1 Installation Workflow

**Phase 1: User Input**
```
Welcome
  ↓
License Agreement  
  ↓
Installation Folder (default: C:\Program Files\Local AI Assistant)
  ↓
Configuration
  ├─ User Name (default: %USERNAME%)
  ├─ Windows Docs folder (default: C:\AI_Test_Documents)  
  ├─ Email (optional, can skip)
  │  ├─ Email Host
  │  ├─ Email User  
  │  ├─ Email Password
  │  └─ Sender Name
  └─ Ready to Install

Phase 2: Installation
  ├─ Copy .exe
  ├─ Create .env with config
  ├─ Create Start Menu shortcuts
  ├─ Create Desktop shortcut
  ├─ Add Uninstall entry
  ├─ Register Windows association (.aiagent files)
  └─ Run post-install setup

Phase 3: Finishing
  ├─ Show Release Notes
  ├─ Option: Run app now
  ├─ Option: Show setup guide
  └─ Option: Open documentation
```

### 9.2 Installer Features

| Feature | Status | Details |
|---------|--------|---------|
| Installer for .exe | ✅ | `LocalAIAgent_Installer.iss` |
| Desktop shortcut | ✅ | Included in installer |
| Start Menu shortcut | ✅ | Included in installer |
| .env auto-configuration | ✅ | Via wizard |
| System PATH integration | ⚠️ | Optional (not needed) |
| Uninstaller | ✅ | Removes app + shortcuts, preserves data |
| Repair/Modify | ✅ | Inno Setup automatic |

### 9.3 Post-Install Actions

**Handled automatically:**
1. ✅ Create `C:\Program Files\Local AI Assistant\` folder
2. ✅ Copy `LocalAIAgent.exe`
3. ✅ Create `.env` from wizard inputs
4. ✅ Create `data/` subdirectories
5. ✅ Register file type associations (optional)

**User must do manually:**
1. ⚠️ **Provide `credentials.json`** from Google Cloud Console
2. ⚠️ **Start Ollama daemon** (separate download from ollama.ai)
3. ⚠️ **Install Tesseract** (if PDF/OCR needed)
4. ⚠️ **OAuth login** on first email use

---

## 10. ENVIRONMENT VARIABLE HANDLING FOR DEPLOYMENT

### 10.1 Recommended: .env File (Not System Env)

**Why:** 
- Survives Windows updates
- Easy to edit (notepad)
- No restart needed to update
- Can be distributed with installer

**Location:** `{app}\.env` (same folder as `.exe`)

### 10.2 Example .env (Generated by Installer)

```env
# Auto-generated by installer on 2026-04-17
# Edit and restart the app for changes to take effect

# ─── User Identity ───────────────────────
USER_NAME=John Doe

# ─── Windows Documents Indexing ──────────
WINDOWS_DOCS_PATH=C:\AI_Test_Documents
# WINDOWS_DOCS_SUBFOLDERS=Work,Projects    # (optional, comma-separated)

# ─── Email Configuration (Optional) ──────
# Leave blank to skip email features
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASS=your-app-password
EMAIL_FROM=Your Name

# ─── OCR (PDF Text Extraction) ───────────
# Path to Tesseract executable (if installed)
# Leave blank to skip OCR
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe

# ─── Model Configuration (Advanced) ──────
MODEL_NAME=llama3
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DEVICE=cpu

# ─── Logging ─────────────────────────────
LOG_LEVEL=INFO
# LOG_FILE=app.log              # (optional, creates file in data/logs/)
```

### 10.3 Installer: Collecting Configuration

**Inno Setup creates this** via wizard:
```pascal
[Code]
procedure ConfigureUserName;
begin
  { Prompt for user name }
  UserNameVar := GetUserNameString();  // Default to Windows username
  { Store in .env }
  WriteToEnvFile('USER_NAME=' + UserNameVar);
end;
```

---

## 11. DEPLOYMENT CHECKLIST

### 11.1 Pre-Build Environment

- [ ] Python 3.11.x installed locally
- [ ] `pip install -r requirements.txt` completed
- [ ] `pip install pyinstaller` (latest version)
- [ ] `pip install inno-setup-utils` (optional, for signing)
- [ ] Inno Setup 6.2+ installed on build machine

### 11.2 Build Phase

- [ ] Run PyInstaller: `pyinstaller LocalAIAgent_enhanced.spec --onefile`
- [ ] Verify `.exe` created: `dist\LocalAIAgent.exe` (~350 MB)
- [ ] Test `.exe` on build machine (check startup, run `--help`)
- [ ] Test `.exe` on clean VM without dev environment
- [ ] Sign `.exe` with certificate (optional)
- [ ] Verify with antivirus scanner (VirusTotal)

### 11.3 Installer Creation

- [ ] Build `.iss` script with Inno Setup compiler
- [ ] Output: `LocalAIAgent_Installer.exe` (~100 MB)
- [ ] Test uninstalled → installed → run → uninstalled
- [ ] Verify all files copied correctly
- [ ] Verify shortcuts created in Start Menu + Desktop
- [ ] Verify `.env` configuration saved

### 11.4 Release Packaging

- [ ] Create release folder with:
  - `LocalAIAgent_Installer.exe`
  - `INSTALL_INSTRUCTIONS.txt` (quick start)
  - `README.md` (documentation)
  - `SETTINGS.txt` (configuration guide)
  - `TROUBLESHOOTING.md` (common issues + fixes)

### 11.5 End-User Installation

- [ ] Download installer from website
- [ ] Run `LocalAIAgent_Installer.exe` as admin (Windows protections)
- [ ] Follow installer wizard
- [ ] Verify app launches: `Start Menu → Local AI Assistant`
- [ ] Provide `credentials.json` (Google OAuth setup outside installer)
- [ ] Start Ollama daemon before using LLM features
- [ ] Install Tesseract for PDF OCR (if needed)

### 11.6 First Run Post-Installation

**Expected behavior:**
1. ✅ App starts, shows welcome message
2. ✅ DocumentService loads documents (if any in `data/documents/`)
3. ✅ VectorStoreService starts indexing (background)
4. ✅ ReminderService loads reminders
5. ✅ CLI prompt appears: `You: `
6. ✅ User can type commands

**Common warnings (OK):**
- ⚠️ "Ollama unreachable" (if not started) → User starts Ollama
- ⚠️ "No Gmail token" (expected first run) → User logs in via OAuth
- ⚠️ "Tesseract not found" (if not installed) → User installs or skips OCR

---

## 12. DIRECTORY STRUCTURE AFTER INSTALLATION

### 12.1 Installation Directory: `C:\Program Files\Local AI Assistant\`

```
C:\Program Files\Local AI Assistant\
├── LocalAIAgent.exe         (main executable, 350 MB)
├── .env                     (runtime configuration)
├── README.txt               (quick start guide)
└── RELEASE_NOTES.txt        (version history)
```

### 12.2 User Data Directory: `%APPDATA%\LocalAIAgent\data\`

```
C:\Users\{USERNAME}\AppData\Roaming\LocalAIAgent\data\
├── documents/               (user uploads documents here)
├── audio/                   (audio files for transcription)
├── logs/                    (application logs)
├── vector_store/            (document embeddings, grows over time)
├── vector_store_audio/      (audio transcription embeddings)
├── vector_store_win_docs/   (Windows Documents embeddings)
├── credentials.json         (USER setup required - Google OAuth)
├── gmail_token.json         (auto-created on first Gmail login)
├── memory.json              (auto-created, conversation history)
├── reminders.json           (auto-created, user reminders)
├── drafts.json              (auto-created, email drafts)
├── email_cache.json         (auto-created, Gmail metadata)
├── granted_folders.json     (auto-created, folder permissions)
├── file_index.db            (auto-created, SQLite search index)
└── win_docs_index_state.json (auto-created, indexing state)
```

### 12.3 HuggingFace Cache: `~/.cache/huggingface/`

```
C:\Users\{USERNAME}\.cache\huggingface\
└── hub/
    ├── sentence-transformers/all-MiniLM-L6-v2/  (~90 MB)
    └── ... (other models as used)
```

---

## 13. EXTERNAL DEPENDENCIES FOR END USERS

### 13.1 MUST INSTALL (Manually, Outside Installer)

| Dependency | Download | Installation | Notes |
|-----------|----------|--------------|-------|
| **Ollama** | https://ollama.ai | Download + run installer | Core LLM backend, runs as service/daemon |

### 13.2 OPTIONAL (For Advanced Features)

| Dependency | Download | Installation | Notes |
|-----------|----------|--------------|-------|
| **Tesseract-OCR** | https://github.com/UB-Mannheim/tesseract/wiki | Run installer to `C:\Program Files\Tesseract-OCR\` | Enables PDF text extraction via OCR |
| **Poppler** | https://github.com/oschwartz10612/poppler-windows | Extract to PATH or `C:\Program Files\poppler\bin` | Alternative OCR backend (slower) |

### 13.3 Already Bundled in .exe

- ✅ All Python packages (from requirements.txt)
- ✅ PyTorch (large, but needed)
- ✅ Transformers (embeddings)
- ✅ ChromaDB (vector store)
- ✅ All agents + services

---

## 14. PERFORMANCE CHARACTERISTICS

### 14.1 Startup Time

| Component | Time | Notes |
|-----------|------|-------|
| Extract .exe from %TEMP% | 2-5 sec | Only on first Run (onefile) |
| Import Python + libraries | 5-8 sec | PyTorch initialization slow |
| Load DocumentService | 1-3 sec | Depends on document count |
| Start VectorStore service | 2-5 sec | HF models download on first run (300+ MB) |
| Start reminder service | <1 sec | Fast JSON load |
| **Total startup** | **10-22 sec** | Typical range |

**Optimization:** Pre-cache models during installer? (Future enhancement)

### 14.2 Runtime Memory

| Component | Typical RAM | Notes |
|-----------|------------|-------|
| Python runtime + packages | ~200 MB | Base footprint |
| Transformer models | ~100-300 MB | Embedding model in memory |
| ChromaDB vector store | ~50-500 MB | Depends on document count |
| Document cache | ~50-200 MB | Active documents in memory |
| **Total runtime RAM** | **400-1000 MB** | Typical |

**Minimum:** 1 GB RAM  
**Recommended:** 4+ GB RAM (for fast performance)

### 14.3 Subsequent Startups (Cached)

| Component | Time |
|-----------|------|
| Skip %TEMP% extraction (cached) | 0 sec |
| Load Python + libraries | 3-5 sec |
| Load services | 2-4 sec |
| **Total (cached)** | **5-9 sec** |

---

## 15. TROUBLESHOOTING GUIDE FOR END USERS

### 15.1 "App won't start: Python error"

**Cause:** PyInstaller spec missing hidden imports  
**Solution:** Check `LocalAIAgent_enhanced.spec`, rebuild with correct hiddenimports

### 15.2 "DLL not found: vcruntime140.dll"

**Cause:** Visual C++ runtime missing  
**Solution:** Download Visual C++ Redistributable from Microsoft

### 15.3 "Ollama unreachable at localhost:11434"

**Cause:** Ollama service not running  
**Solution:** Download Ollama from https://ollama.ai, install, run: `ollama serve`

### 15.4 "ImportError: chromadb not found"

**Cause:** PyInstaller didn't bundle chromadb  
**Solution:** Add `chromadb` to hiddenimports in spec file

### 15.5 "Email features not working"

**Cause:** Google OAuth credentials not configured  
**Solution:** Provide `data/credentials.json` with Google OAuth client ID from Google Cloud Console

### 15.6 "PDF OCR not working"

**Cause:** Tesseract not installed or not found  
**Solution:** Install Tesseract to `C:\Program Files\Tesseract-OCR\` or set `TESSERACT_CMD` in `.env`

### 15.7 "Antivirus blocks .exe"

**Cause:** Windows Defender or other AV flags binary as PUP  
**Solution:** Add exception, or code-sign the executable (requires certificate)

---

## 16. SECURITY CONSIDERATIONS

### 16.1 Credential Management

**In .env (on disk):**
- ⚠️ Email password stored in plaintext in `.env`
- **Mitigation:** Restrict file permissions via Inno Setup (owner read-only)
- **Better:** Use environment variables (system-level) or Windows Credential Manager (future)

**In memory:**
- ✅ Credentials only loaded at startup
- ✅ No logging of credentials
- ✅ Gmail OAuth tokens stored in `gmail_token.json` (standard OAuth)

### 16.2 Folder Access Control

- ✅ Windows Documents folder access request mandatory
- ✅ Permission granted for 5 minutes, then expires
- ✅ Granted permissions saved in `granted_folders.json`
- ✅ User can revoke via CLI: `revoke C:\path\to\folder`

### 16.3 Data Privacy

- ✅ All processing done locally (no cloud)
- ✅ Ollama runs on localhost, not sent to external servers
- ✅ Gmail data cached locally but not persistent
- ✅ Vector store is searchable locally only

### 16.4 Code Signing (Optional but Recommended)

```bash
# Sign the .exe with certificate
signtool.exe sign /f certificate.pfx /p password /t timestamp_server LocalAIAgent.exe
```

**Benefits:**
- Reduces Windows Defender false positives
- Users see publisher name instead of "Unknown Publisher"
- Professional appearance

---

## 17. VERSION UPDATES & MAINTENANCE

### 17.1 Updating Installed Version

**Option 1: Overwrite via Installer**
```
Run newest LocalAIAgent_Installer.exe
→ Detects existing installation
→ Offers upgrade option
→ Preserves data/ folder
```

**Option 2: Manual Replacement**
```
1. Close running app
2. Backup data/ folder
3. Copy new LocalAIAgent.exe to {app}\
4. Restart app
```

### 17.2 Preserving User Data

**Automatic (by design):**
- ✅ All user data in `%APPDATA%\LocalAIAgent\data\`
- ✅ Installer doesn't touch data/
- ✅ Uninstall option: "Keep user data?" → removes app, keeps data

### 17.3 Configuration Migration

**If .env changes between versions:**
1. Show message: "Configuration format updated"
2. Auto-migrate old → new format
3. Preserve user values
4. Add new defaults for new options

---

## 18. DEPLOYMENT SUMMARY TABLE

| Aspect | Details |
|--------|---------|
| **Entry Point** | `main.py` (CLI REPL) |
| **Build Tool** | PyInstaller 6.1+ (spec: `LocalAIAgent_enhanced.spec`) |
| **Installer** | Inno Setup 6.2+ (script: `LocalAIAgent_Installer.iss`) |
| **Output Exe** | `LocalAIAgent.exe` (~350 MB, single-file) |
| **Output Installer** | `LocalAIAgent_Installer.exe` (~100 MB) |
| **Install Location** | `C:\Program Files\Local AI Assistant\` |
| **Data Location** | `%APPDATA%\LocalAIAgent\data\` |
| **Config** | `.env` file in install directory |
| **Python Version** | 3.11+ |
| **Windows** | Windows 10/11 (64-bit) |
| **Startup Time** | 10-22 sec (first run), 5-9 sec (cached) |
| **RAM Required** | 1 GB minimum, 4+ GB recommended |
| **Network** | Optional (Gmail, HF model downloads) |
| **External Services** | Ollama (required), Tesseract (optional), Poppler (optional) |

---

## 19. NEXT STEPS

1. ✅ Review this plan
2. ✅ Generate PyInstaller spec: `LocalAIAgent_enhanced.spec`
3. ✅ Generate Inno Setup script: `LocalAIAgent_Installer.iss`
4. ✅ Create `.env.example` template
5. ✅ Build locally: `pyinstaller LocalAIAgent_enhanced.spec --onefile`
6. ✅ Test `.exe` on clean Windows VM
7. ✅ Build installer: Inno Setup compiler
8. ✅ Test installer: uninstall → install → run
9. ✅ Package for release with docs
10. ✅ Publish with install instructions

---

## Appendix: File Manifest for Release

```
LocalAIAssistant_Release_1.0/
├── LocalAIAgent_Installer.exe       (main installer)
├── LocalAIAgent_Portable.exe        (optional standalone exe)
├── INSTALL_INSTRUCTIONS.txt         (step-by-step guide)
├── CONFIGURATION_GUIDE.md           (how to set up credentials)
├── TROUBLESHOOTING.md               (common problems + solutions)
├── RELEASE_NOTES.txt                (version history)
├── API_KEYS_SETUP.md                (Google OAuth instructions)
└── EXTERNAL_DEPENDENCIES.md         (Ollama, Tesseract, Poppler)
```

---

**END OF DEPLOYMENT PLAN**

*For questions or issues, see `TROUBLESHOOTING.md` or visit project repository.*
