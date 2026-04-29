# Step-by-Step Windows Deployment Instructions

## Complete Guide to Build, Package, and Deploy Local AI Assistant

**Last Updated:** April 2026  
**Target:** Windows 10/11 (64-bit)  
**Audience:** Developers & DevOps engineers

---

## PART 1: BUILDING THE EXECUTABLE

### Prerequisites

Before you begin, ensure you have:
- Python 3.11+ installed
- Git (for version control)
- Visual C++ Build Tools (for PyTorch compilation)
- ~5 GB free disk space (for build environment)
- Administrator access on build machine

### Step 1.1: Prepare Build Environment

```powershell
# Open PowerShell as Administrator

# Clone/enter project directory
cd C:\Users\Sandeep\OneDrive\Documents\GitHub\Local_AI_Agent1.1\local_ai_assistant

# Create clean Python virtual environment (optional, but recommended)
python -m venv build_venv
.\build_venv\Scripts\Activate.ps1

# Install production dependencies
pip install -r requirements.txt

# Install build tools
pip install pyinstaller==6.1.0
pip install inno-setup-utils  # Optional, for Inno Setup integration
```

### Step 1.2: Build the Executable with PyInstaller

```powershell
# Activate venv if not already
.\build_venv\Scripts\Activate.ps1

# Change to project directory
cd C:\Users\Sandeep\OneDrive\Documents\GitHub\Local_AI_Agent1.1\local_ai_assistant

# Run PyInstaller
pyinstaller LocalAIAgent_enhanced.spec --onefile

# Expected output:
# - dist\LocalAIAgent.exe                    (~350 MB)
# - build\LocalAIAgent\                      (intermediate files, can delete)
# - LocalAIAgent.spec.build_info             (metadata)
```

**Build time:** 5-15 minutes (depends on machine)  
**Output size:** ~350 MB (single .exe file)

### Step 1.3: Verify the Executable

```powershell
# Test that .exe runs and shows help
.\dist\LocalAIAgent.exe --help

# Expected output:
# Usage: main [OPTIONS] COMMAND [ARGS]...
# ...

# Test basic startup (should show "You:" prompt)
# Press Ctrl+C to exit
.\dist\LocalAIAgent.exe
```

### Step 1.4: Test on Clean Windows VM (Recommended)

```
1. Create fresh Windows 10/11 Virtual Machine
   - 2+ CPU cores
   - 4+ GB RAM
   - 20+ GB disk space
   - No dev tools installed

2. Copy dist\LocalAIAgent.exe to VM

3. Try to run it:
   .\LocalAIAgent.exe
   
4. Expected:
   ✅ Shows welcome message
   ✅ Asks for user input
   ✅ No missing DLL errors
   ❌ If DLL error → missing hidden import in spec file
```

---

## PART 2: CREATING THE INSTALLER

### Step 2.1: Install Inno Setup

```
1. Download: https://jrsoftware.org/isdl.php
   (Get latest version, e.g., innosetup-6.2.2.exe)

2. Run installer, use default options

3. Verify installation:
   "C:\Program Files (x86)\Inno Setup 6\iscc.exe" /?
```

### Step 2.2: Prepare Installer Resources

```powershell
# Ensure these files exist in project root:

# 1. Copy the built executable
Copy-Item ".\dist\LocalAIAgent.exe" ".\LocalAIAgent.exe"

# 2. Ensure icon.ico exists (optional but recommended)
#    If not, download a 32x32 icon and save as icon.ico
#    Or create a placeholder:
#    - Use any existing icon file
#    - Or Inno Setup will use generic Windows icon

# 3. Ensure documentation files exist:
#    - WINDOWS_DEPLOYMENT_PLAN.md      (should already exist)
#    - TROUBLESHOOTING.md              (if not, see part 3)
```

### Step 2.3: Compile Installer with Inno Setup

```powershell
# Open Inno Setup GUI
# File → Open → LocalAIAgent_Installer.iss

# Or compile from command line:
& "C:\Program Files (x86)\Inno Setup 6\iscc.exe" `
  "C:\path\to\LocalAIAgent_Installer.iss" `
  /O"Output"

# Expected output:
# Output\LocalAIAgent_Installer.exe (~100 MB)
```

**Compilation time:** 2-5 minutes

### Step 2.4: Test Installer

```powershell
# Run the installer
.\Output\LocalAIAgent_Installer.exe

# Follow wizard:
# 1. Accept license
# 2. Choose install folder (default: C:\Program Files\Local AI Assistant)
# 3. Configure settings:
#    - User Name: Your Name
#    - Windows Docs Path: C:\AI_Test_Documents
#    - Email (optional): your-email@gmail.com
#    - Email Password: ****
#    - Tesseract Path: (if installed)
# 4. Click Install
# 5. Choose post-install actions:
#    ☑ Edit Configuration File
#    ☑ Open Installation Folder

# Verify installation:
dir "C:\Program Files\Local AI Assistant\"

# Should contain:
# - LocalAIAgent.exe
# - .env (auto-generated with your settings)
# - README.md
```

---

## PART 3: FINAL PACKAGING FOR RELEASE

### Step 3.1: Create Release Bundle

```powershell
# Create release directory
mkdir "LocalAIAssistant_Release_1.0"
cd "LocalAIAssistant_Release_1.0"

# Copy installer
Copy-Item "..\Output\LocalAIAgent_Installer.exe" "."

# Copy documentation (create these files if not present)
Copy-Item "..\WINDOWS_DEPLOYMENT_PLAN.md" "."
Copy-Item "..\README.md" "README.md"
New-Item "INSTALLATION_GUIDE.txt" -ItemType File -Value @"
LOCAL AI ASSISTANT - QUICK START

1. Run the installer:
   LocalAIAgent_Installer.exe

2. Follow the configuration wizard

3. IMPORTANT: Download and start Ollama BEFORE using the app:
   https://ollama.ai
   
4. (Optional) For PDF/image OCR, install Tesseract-OCR:
   https://github.com/UB-Mannheim/tesseract/wiki

5. Run the app from Start Menu:
   Start → Local AI Assistant → Local AI Assistant

6. First run will download ~300 MB of models (requires internet)

TROUBLESHOOTING:
- "Ollama unreachable" → Start Ollama daemon
- "DLL not found" → Reinstall, check Windows updates
- "Antivirus blocked" → Add exception in Windows Defender

Need help? See TROUBLESHOOTING.md
"@

# Create checksums (optional, for integrity verification)
certutil -hashfile "..\Output\LocalAIAgent_Installer.exe" SHA256 > "CHECKSUMS.txt"
```

### Step 3.2: Create Troubleshooting Guide

Create file: `TROUBLESHOOTING.md`

```markdown
# Troubleshooting Guide

## Problem: "Ollama unreachable at localhost:11434"

**Cause:** Ollama service not running  
**Solution:**
1. Download Ollama from https://ollama.ai
2. Install (use default settings)
3. Start Ollama:
   - Windows: Ollama will auto-start (check system tray)
   - Or manually: Open PowerShell and run: ollama serve
4. Verify: Open http://localhost:11434 in browser (should show version info)
5. Retry the application

## Problem: "ImportError: DLL load failed"

**Cause:** Missing Visual C++ runtime  
**Solution:**
1. Download Visual C++ Redistributable:
   https://support.microsoft.com/en-us/help/2977003
2. Install (choose 64-bit version)
3. Restart application

## Problem: "Tesseract not found"

**Cause:** OCR not installed or wrong path  
**Solution:**
1. Option A (Recommended): Leave TESSERACT_CMD blank in .env
   - App will use PDF text extraction instead (no OCR for scans)
   - Faster startup
   
2. Option B: Install Tesseract
   - Download: https://github.com/UB-Mannheim/tesseract/wiki
   - Install to standard location: C:\Program Files\Tesseract-OCR
   - Restart app (or set TESSERACT_CMD in .env)

## Problem: "Google Auth failed" / No email support

**Cause:** Google OAuth credentials not configured  
**Solution:**
1. Go to Google Cloud Console: https://console.cloud.google.com
2. Create new project
3. Enable Gmail API
4. Create OAuth 2.0 credentials (Desktop application)
5. Download credentials.json
6. Copy to: C:\Users\{Username}\AppData\Roaming\LocalAIAgent\data\credentials.json
7. Restart app and try email features

## Problem: Antivirus blocks .exe

**Cause:** Windows Defender flags PyInstaller binary  
**Solution:**
1. Windows Defender:
   - Right-click .exe → Properties
   - Check "Unblock" checkbox
   - Click OK
   
2. Or add exception:
   - Windows Defender → Manage settings → Add exclusions
   - Choose "Files" → Select LocalAIAgent.exe

## Problem: Very slow startup (20+ seconds)

**Cause:** PyTorch lazy loading, first-run model download  
**Solution:**
1. First startup is slow (downloads 300 MB)
2. Subsequent startups should be 5-10 seconds
3. If slow on all startups, check:
   - Disk space (need ~500 MB free)
   - RAM (need ~1 GB free)
   - CPU usage (background processes?)

## Problem: "Windows Docs folder not accessible"

**Cause:** Permissions issue or wrong path  
**Solution:**
1. Verify path exists: Explorer → C:\AI_Test_Documents
2. Check permissions:
   - Right-click folder → Properties → Security
   - Ensure your user account has "Read" permission
3. Edit .env:
   - WINDOWS_DOCS_PATH=C:\path\to\authorized\folder
4. Restart app

## More Help

Check application logs:
C:\Users\{Username}\AppData\Roaming\LocalAIAgent\data\logs\

For detailed debugging, set in .env:
LOG_LEVEL=DEBUG
```

### Step 3.3: Create .env Template

Copy `.env.example` to release (already done):
```powershell
Copy-Item ".\.env.example" ".\LocalAIAssistant_Release_1.0\CONFIG_TEMPLATE.txt"
```

### Step 3.4: Generate Checksums & Signing (Optional)

```powershell
# Create SHA256 checksums for integrity verification
Get-FileHash "LocalAIAssistant_Release_1.0\LocalAIAgent_Installer.exe" -Algorithm SHA256 | `
  Out-File "LocalAIAssistant_Release_1.0\CHECKSUMS.txt"

# Optional: Code-sign the executable (requires certificate)
$cert = Get-ChildItem -Path Cert:\CurrentUser\My -CodeSigningCert | Select-Object -First 1
Set-AuthenticodeSignature -FilePath "LocalAIAssistant_Release_1.0\LocalAIAgent_Installer.exe" -Certificate $cert

# Verify signature
Get-AuthenticodeSignature "LocalAIAssistant_Release_1.0\LocalAIAgent_Installer.exe"
```

---

## PART 4: END-USER INSTALLATION WALKTHROUGH

### Step 4.1: Pre-Installation Checklist

**User should verify:**
- [ ] Windows 10/11 (64-bit)
- [ ] 4+ GB RAM available
- [ ] 5+ GB free disk space
- [ ] Administrator account
- [ ] Internet connection (for first-run model download)

### Step 4.2: Install Ollama (REQUIRED)

```
1. Download: https://ollama.ai
2. Click "Download for Windows"
3. Run installer
4. Accept defaults
5. Ollama will start automatically
6. Look for Ollama icon in system tray (bottom-right)
7. Verify: http://localhost:11434 shows "Ollama is running"
```

### Step 4.3: Install Local AI Assistant

```
1. Download LocalAIAgent_Installer.exe from release

2. Right-click → Run as Administrator
   (Windows Protection may ask "More options → Run anyway")

3. Welcome screen:
   Click "Next"

4. License Agreement:
   Read and click "I Agree"

5. Installation Folder:
   Default is fine: C:\Program Files\Local AI Assistant
   Click "Next"

6. Configuration Wizard:
   
   a) User Name:
      Enter your name (or leave as default)
   
   b) Windows Documents Path:
      Where to index documents from
      Default: C:\AI_Test_Documents (create this folder first!)
      Or choose: C:\Users\{YourName}\Documents
   
   c) Email Configuration (Optional):
      - Email Host: smtp.gmail.com (for Gmail)
      - Email Port: 587
      - Email User: your-email@gmail.com
      - Email Password: (app-specific password, not account password)
      - Sender Name: Your Full Name
      
      Leave blank if you don't want email features
   
   d) Tesseract Path (Optional):
      If you installed Tesseract: C:\Program Files\Tesseract-OCR\tesseract.exe
      Leave blank otherwise
   
   Click "Next"

7. Ready to Install:
   Review settings, click "Install"

8. Installation Complete:
   Options appear:
   - [✓] Edit Configuration File (.env)
   - [✓] Open Installation Folder
   
   Click "Finish"

9. First Run:
   - Text editor opens with .env (editable configuration)
   - Explorer opens showing C:\Program Files\Local AI Assistant\
```

### Step 4.4: Verify Installation

```powershell
# Check files installed correctly
dir "C:\Program Files\Local AI Assistant\"

# Should contain:
# - LocalAIAgent.exe
# - .env
# - README.md

# Verify user data folder created
dir "$env:APPDATA\LocalAIAgent\data\"

# Should be mostly empty on first run (except pre-created subdirs)
```

### Step 4.5: First Run

```
1. Click Start Menu → Local AI Assistant → Local AI Assistant

2. App starts, shows:
   =======================================
   Welcome to Local AI Assistant
   =======================================
   Loading documents...
   Starting services...
   You:
   
3. If OK:
   ✅ App is running correctly
   ✅ Type: hello
   ✅ You should get a response
   
4. If you see warning "Ollama unreachable":
   ❌ Ollama not running
   ⚠️ Click on Ollama icon in system tray to start service
   ⚠️ Or run: ollama serve (in PowerShell)

5. Exit app:
   Type: exit
   Or press: Ctrl+C
```

---

## PART 5: UPDATES & MAINTENANCE

### Step 5.1: Updating to Newer Version

**Option A: Using Installer (Recommended)**

```
1. Download new LocalAIAgent_Installer.exe

2. Run installer (same as fresh install)
   - Detects existing version
   - Offers to upgrade
   
3. Preserves:
   ✅ .env configuration
   ✅ User data (documents, vector stores, reminders)
   ✅ Gmail tokens and credentials

4. Updates:
   ✅ LocalAIAgent.exe
   ⚠️ Hidden imports and Python packages
```

**Option B: Manual (Advanced)**

```powershell
# 1. Close running application
# 2. Backup data folder
Copy-Item "$env:APPDATA\LocalAIAgent\data" -Destination "backup_$(Get-Date -Format 'yyyyMMdd')" -Recurse

# 3. Copy new exe over old
Copy-Item ".\dist\LocalAIAgent.exe" "C:\Program Files\Local AI Assistant\LocalAIAgent.exe" -Force

# 4. Restart application
& "C:\Program Files\Local AI Assistant\LocalAIAgent.exe"
```

### Step 5.2: Backup User Data

```powershell
# Before major updates, backup data folder

$DataPath = "$env:APPDATA\LocalAIAgent\data"
$BackupPath = "$env:DESKTOP\LocalAIAgent_Backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"

Copy-Item $DataPath -Destination $BackupPath -Recurse

Write-Host "Backup created at: $BackupPath"
```

### Step 5.3: Migration Between Machines

```powershell
# Transfer installation to new computer:

# 1. On OLD computer:
#    - Close app
#    - Backup data: see Step 5.2
#    - Note .env settings

# 2. On NEW computer:
#    - Install fresh using installer
#    - Copy old data folder:
Copy-Item "old_backup\data" -Destination "$env:APPDATA\LocalAIAgent" -Recurse -Force
#    - Update .env if needed

# 3. Restart app
#    - All data, settings, and prompts will be restored
```

---

## PART 6: DEPLOYMENT VALIDATION CHECKLIST

### Pre-Build
- [ ] Python 3.11 installed
- [ ] requirements.txt up-to-date
- [ ] PyInstaller installed
- [ ] No syntax errors in any .py file
- [ ] All tests pass (if you have tests)

### Build Phase
- [ ] PyInstaller spec file reviewed
- [ ] Build completes without errors
- [ ] .exe file created (~350 MB)
- [ ] .exe runs on build machine
- [ ] .exe runs on clean VM without dev tools

### Installer Phase
- [ ] Inno Setup installed
- [ ] Installer.iss script reviewed
- [ ] Installer compiles successfully
- [ ] Installer runs without errors
- [ ] Configuration wizard collects inputs
- [ ] .env file created correctly
- [ ] Shortcuts created in Start Menu + Desktop
- [ ] Uninstaller works (app + shortcuts removed)

### Testing
- [ ] Manual install/uninstall cycle works
- [ ] Config wizard inputs saved to .env
- [ ] Can edit .env in notepad
- [ ] App launches from Start Menu
- [ ] Data persists between restarts
- [ ] Help/docs accessible from Start Menu

### Release
- [ ] Installer signed (optional but recommended)
- [ ] Checksums generated
- [ ] Installation guide written
- [ ] Troubleshooting guide ready
- [ ] Release notes prepared
- [ ] Backup made before publishing

---

## PART 7: COMMON BUILD ISSUES & SOLUTIONS

### Issue: "ModuleNotFoundError: No module named 'X'"

**Cause:** Package not in requirements.txt or hidden import not in spec file

**Solution:**
```bash
# 1. Verify package installed
pip show package_name

# 2. Add to requirements.txt if missing
pip list > temp.txt  # Check current env

# 3. Add to hiddenimports in spec file
# 4. Rebuild
pyinstaller LocalAIAgent_enhanced.spec --onefile
```

---

### Issue: "This program cannot be run - missing DLL"

**Cause:** Visual C++ runtime not installed on target machine

**Solution:**
- Download: https://support.microsoft.com/en-us/help/2977003
- Install Visual C++ Redistributable (64-bit)

---

### Issue: Executable is 500+ MB (too large)

**Cause:** PyTorch or other large packages included unnecessarily

**Solution:**
```python
# Check what's bundled
pyinstaller --analyze LocalAIAgent_enhanced.spec

# Remove unused packages from excludes
# Example: if not using TensorFlow, add to excludes:
# 'tensorflow', 'keras'
```

---

### Issue: "Antivirus quarantines the .exe"

**Cause:** PyInstaller binary flagged as suspicious

**Solution:**
1. Code-sign the executable (requires certificate)
2. Exclude in Windows Defender (Virus & threat protection → Exclusions)
3. Test with VirusTotal before release

---

## Final Notes

- **Estimated total time:** 30-60 minutes (first time), 10-15 min (subsequent)
- **Disk space needed:** 5+ GB for build environment, 350 MB for installer
- **Network:** Only on first run (300 MB model download)
- **Support:** See TROUBLESHOOTING.md for common issues

---

**You're ready to deploy!** 🚀

Questions? Check WINDOWS_DEPLOYMENT_PLAN.md for detailed specifications.
