# Troubleshooting Guide: Local AI Assistant for Windows

**For:** End Users & Developers    
**Version:** 1.0  
**Last Updated:** April 2026  

---

## Common Issues & Solutions

### 🔴 CRITICAL: App Won't Start

---

#### Error: "Ollama is unreachable at localhost:11434"

**Symptom:**
```
Error: Ollama unreachable at http://localhost:11434
Unable to reach LLM backend. Please ensure Ollama is installed and running.
```

**Causes:**
- Ollama not installed
- Ollama not running (no daemon/service started)
- Ollama installed but configured differently
- Firewall blocking localhost access (rare)

**Solutions (in order of likelihood):**

1. **Check if Ollama is installed:**
   ```powershell
   # In PowerShell, try to connect
   (Invoke-WebRequest http://localhost:11434 -ErrorAction SilentlyContinue).StatusCode
   
   # If fails, Ollama not running
   ```

2. **Download and Install Ollama:**
   - Download: https://ollama.ai
   - Click "Download for Windows"
   - Run installer: `ollama-windows-X.X.X.exe`
   - Accept default installation path
   - Installer will auto-start Ollama service
   - Check system tray for Ollama icon (llama symbol)

3. **Start Ollama Service (if installed but not running):**
   ```powershell
   # Method A: Via system tray icon
   # Look for llama icon in bottom-right system tray
   # If not visible, Ollama may need restart
   
   # Method B: Via command line
   ollama serve
   
   # If command not found, add to PATH:
   # Path: C:\Users\{USERNAME}\AppData\Local\Programs\Ollama\
   # Or restart computer to refresh PATH
   ```

4. **Verify Ollama is listening:**
   ```powershell
   # Open browser and go to:
   http://localhost:11434
   
   # Should display version info (plain text, not HTML)
   # If yes, Ollama is working
   # If connection refused, Ollama not actually running
   ```

5. **Check Firewall:**
   ```powershell
   # Unlikely, but try disabling temporarily
   # Windows Defender Firewall → Allow app through firewall
   # Ensure Ollama.exe is in "Allowed apps"
   ```

**Resolution:**
- ✅ Ollama icon in system tray
- ✅ http://localhost:11434 shows version in browser
- ✅ Restart Local AI Assistant
- ✅ Should work now

**If still not working:** Reinstall Ollama, restart computer

---

### 🔴 CRITICAL: DLL/Runtime Errors

---

#### Error: "vcruntime140.dll not found"

**Symptom:**
```
The code execution cannot proceed because vcruntime140.dll was not found.
Reinstalling the program may fix this problem.
```

**Cause:**
- Visual C++ runtime library missing
- Required by PyTorch (one of the included packages)

**Solution (1-2 minutes):**

1. Download Visual C++ Redistributable:
   - Go: https://support.microsoft.com/en-us/help/2977003
   - Click "Latest supported Visual C++ Redistributable"
   - Choose **64-bit** version (`vc_redist.x64.exe`)

2. Install:
   ```powershell
   .\vc_redist.x64.exe
   # Accept license
   # Click Install
   # Wait for completion
   # Click Close
   ```

3. Restart your computer (important!)

4. Try running Local AI Assistant again

**If still failing:** Try:
```powershell
# Remove and reinstall
pip install --force-reinstall torch
# Or: Reinstall Local AI Assistant via installer
```

---

#### Error: "DLL not found: {some_library}.dll"

**Cause:** PyInstaller missed a hidden import in the spec file

**Solution (for developers):** 
```python
# In LocalAIAgent_enhanced.spec, add to hiddenimports:
'the_missing_library',

# Then rebuild:
pyinstaller LocalAIAgent_enhanced.spec --onefile
```

**Solution (for users):** 
- Contact developer with the exact DLL name
- Or reinstall app (might fix if installer updated)

---

### ⚠️ WARNING: Features Not Working

---

#### Issue: PDF/Image Text Extraction (OCR) Not Working

**Symptom:**
```
PDF loaded but no text extracted
Or: Warning - Tesseract not found, falling back to PDF text extraction only
```

**Causes:**
- Tesseract-OCR not installed
- Installed in wrong location
- Path specified incorrectly in .env

**Solutions (pick one):**

**Option A: Don't use OCR (simplest)**
```env
# In .env, comment out or remove:
# TESSERACT_CMD=...

# App will use built-in PDF loader (no image/scanned PDF support)
# Restart app
```

**Option B: Install Tesseract (recommended)**
```
1. Download: https://github.com/UB-Mannheim/tesseract/wiki
2. Select latest Windows installer (e.g., tesseract-ocr-w64-setup-v5.X.X.exe)
3. Run installer:
   - Accept license
   - Select installation path (default OK): C:\Program Files\Tesseract-OCR
   - Click Install
   - Click Finish
4. Edit .env:
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
5. Restart Local AI Assistant
6. OCR should work on scanned PDFs now
```

**Option C: Specify custom Tesseract path**
```env
# If you installed elsewhere, edit .env:
TESSERACT_CMD=C:\path\to\your\tesseract.exe

# Common alternate locations:
# TESSERACT_CMD=C:\Program Files\tesseract\tesseract.exe
# TESSERACT_CMD=C:\Users\{USER}\AppData\Local\Tesseract\tesseract.exe
```

**Verify installation:**
```powershell
# Test Tesseract
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version

# Should output version info
# If "command not found", path wrong, reinstall
```

---

#### Issue: Email Features Not Working

**Symptom:**
```
Error: Gmail credentials missing
Or: Cannot connect to SMTP server
Or: Auth failed - Google OAuth
```

**Cause #1: Missing Google OAuth Credentials**

**Solution:**
1. Go to Google Cloud Console: https://console.cloud.google.com
2. Create new project (or use existing)
3. Enable Gmail API:
   - Search "Gmail API" in search bar
   - Click "Enable"
4. Create OAuth 2.0 credentials:
   - Click "Create Credentials"
   - Choose "Gmail API" → "Desktop application"
   - Click "Create"
   - Click "Download JSON"
5. Rename to `credentials.json`
6. Save to data folder:
   ```
   Move credentials.json to:
   C:\Users\{USERNAME}\AppData\Roaming\LocalAIAgent\data\credentials.json
   ```
7. Restart Local AI Assistant
8. Try email feature → Will open browser for OAuth login

**Cause #2: Email Host/Password Wrong**

**Solution:**
1. Edit .env:
   ```
   C:\Program Files\Local AI Assistant\.env
   ```
2. Check settings:
   ```env
   # For Gmail (most common):
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USER=your-email@gmail.com
   EMAIL_PASS=your-app-password   # NOT account password!
   EMAIL_FROM=Your Display Name
   ```
3. **Important:** For Gmail, use "App Passwords" not account password
   - https://support.google.com/accounts/answer/185833
   - Generate app-specific password in Google Account settings
   - Use that in EMAIL_PASS
4. Save .env
5. Restart Local AI Assistant

**Cause #3: SMTP Credentials Wrong**

**Solution:**
```env
# Test your SMTP settings:

# For Outlook/Hotmail:
EMAIL_HOST=smtp.live.com
EMAIL_PORT=587
EMAIL_TLS=true

# For Gmail (alternative):
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_TLS=true

# For corporate/custom SMTP:
# Contact your email admin for correct settings
```

**Verify credentials work:**
```powershell
# Try connecting with telnet (advanced):
# Testing SMTP connection
# This requires knowing proper SMTP auth format
# Easiest: Try sending email via app, check error message

# Or test in Python:
python -c "
import smtplib
try:
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login('your-email@gmail.com', 'your-password')
    print('✅ SMTP login successful!')
    server.quit()
except Exception as e:
    print(f'❌ SMTP Error: {e}')
"
```

---

### ⚠️ WARNING: Performance Issues

---

#### Issue: Very Slow Startup (20+ seconds)

**Symptom:**
```
App takes 30+ seconds to show prompt
Or: "Loading services..." stays for too long
```

**This is normal the FIRST time you run the app!**

**Cause:**
- First-run downloads 300+ MB of models from HuggingFace
- PyTorch initialization
- ChromaDB vector store setup
- Document indexing

**Expected timing:**
- First startup: 10-30 seconds (model download)
- Second startup: 5-9 seconds (cached)
- Subsequent: 5-9 seconds (stable)

**Solutions:**

1. **Just wait** ✅ (It will be faster next time)

2. **If slow on ALL startups:**
   - Check **Disk Space:** Need 500+ MB free
   - Check **RAM Available:** Need 1+ GB free
   - Check **Disk Speed:** Use `HD Tune` or `CrystalDiskInfo` to verify
   - Check **Antivirus:** Scan of .exe may slow startup
   - Disable antivirus scan for .exe:
     ```
     Windows Defender → Manage settings → Add exclusions
     Choose "Files" → LocalAIAgent.exe
     ```

3. **Optimize model loading:**
   ```env
   # Use smaller embedding model (faster)
   EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

   # Use CPU (faster on first load, slower on queries)
   EMBEDDING_DEVICE=cpu
   ```

---

#### Issue: Out of Memory / High RAM Usage

**Symptom:**
```
App crashes after running for a while
Or: "Memory error" when using large documents
Or: System becomes slow/unresponsive
```

**Causes:**
- Large number of documents loaded
- Large vector store in memory
- Long conversation history

**Solutions:**

1. **Check available RAM:**
   ```powershell
   # Task Manager → Performance tab
   # Check "Memory" - should show available RAM
   
   # Or in PowerShell:
   [System.Diagnostics.ProcessInfo]::TotalPhysicalMemory / 1GB
   ```

2. **Free up memory:**
   - Close other applications
   - Restart computer
   - Increase virtual memory (if needed)

3. **Reduce document load:**
   ```env
   # In .env, reduce chunk count:
   VECTOR_STORE_K=5  # (instead of 10)
   CHUNK_SIZE=1000   # (instead of 1500, fewer chunks)
   ```

4. **Recommended minimum environments:**
   - 4 GB RAM (comfortable)
   - 500 MB free disk
   - SSD (not HDD)

---

### 🟡 MINOR: Warnings & Notices

---

#### Warning: "Windows Documents folder not found"

**Symptom:**
```
Warning: WINDOWS_DOCS_PATH directory does not exist
     Path: C:\AI_Test_Documents
     Creating folder...
```

**Cause:** Path configured in `.env` doesn't exist

**Solution:**
```powershell
# Option A: Create the folder
New-Item -Path "C:\AI_Test_Documents" -ItemType Directory -Force

# Option B: Edit .env to point to existing folder
# C:\Program Files\Local AI Assistant\.env
# Change WINDOWS_DOCS_PATH to real path

# Option C: Use existing folder
WINDOWS_DOCS_PATH=C:\Users\{USERNAME}\Documents

# Restart app after changes
```

---

#### Warning: "No documents loaded"

**Symptom:**
```
Loaded 0 document chunk(s).
Or: No documents found in configured path
```

**Cause:** No documents in `data/documents/` folder

**Solution:**
```powershell
# Add documents:
1. Place PDF/DOCX/images in:
   C:\Users\{USERNAME}\AppData\Roaming\LocalAIAgent\data\documents\

2. Or set DOCS_PATH in .env to point to folder with docs

3. Restart app → documents will be loaded and indexed

# Supported formats:
# - PDF (.pdf)
# - Word (.docx)
# - PowerPoint (.pptx)
# - Excel (.xlsx)
# - Images (.jpg, .png, .gif)
# - CSV (.csv)
```

---

#### Warning: "Model not found: {model_name}"

**Symptom:**
```
Warning: Model llama3 not found in Ollama
Attempting to pull model from registry...
```

**Cause:** You've changed MODEL_NAME in .env to a model that Ollama hasn't pulled

**Solution:**
```powershell
# Ollama will auto-download model on first use
# Or manually pull:
ollama pull llama3

# List available models:
ollama list

# Pull different model:
ollama pull mistral:7b   # or any other model
```

---

### 🔧 ADVANCED: Developer/Debug Issues

---

#### Issue: Antivirus Blocks .exe

**Symptom:**
```
Windows Defender warning: "Trojan.Win32.Emotional!c" detected
Or: Antivirus quarantines LocalAIAgent.exe
Or: "Unknown Publisher" warning when running
```

**Cause (not actually malware):**
- PyInstaller-generated binaries flagged as suspicious
- Normal false positive for packed binaries
- Code-signing would fix this (optional)

**Solutions:**

1. **Add exception in Windows Defender (Recommended):**
   ```powershell
   # Windows Defender Firewall → Allow app through firewall
   # OR
   # Settings → Privacy & Security → Virus & threat protection
   # → Manage settings → Add exclusions
   # → Choose "Files" → Select LocalAIAgent.exe
   ```

2. **Temporarily disable antivirus (for testing):**
   ```powershell
   # Settings → Update & Security → Windows Security
   # Click "Virus & threat protection"
   # Click "Manage settings"
   # Toggle "Real-time protection" OFF
   
   # WARNING: Your computer is unprotected! Turn back on after!
   ```

3. **For developers: Code-sign the executable:**
   ```powershell
   # Requires: Code-signing certificate
   # Then use signtool.exe
   signtool.exe sign /f cert.pfx /p password /t timestamp LocalAIAgent.exe
   ```

4. **Check VirusTotal (for validation):**
   - Upload .exe to https://www.virustotal.com
   - Should show mostly clean with no major engine detections
   - PyInstaller false positives are common

---

#### Issue: Can't Edit .env File

**Symptom:**
```
"Access denied" when trying to edit .env
Or: File locked by system
```

**Solution:**
1. Close Local AI Assistant (if running)
2. Right-click .env → Open with → Notepad
3. Edit settings
4. Save (Ctrl+S)
5. Close Notepad
6. Restart Local AI Assistant

**If still locked:**
```powershell
# Check who has file open:
Get-Process | Where-Object { $_.Handles -gt 1000 } | Select-Object Name, Id

# Find .env path:
Get-Item "$env:APPDATA\LocalAIAgent\*.env"

# Close app, then edit
```

---

#### Issue: Models Downloading Very Slowly

**Symptom:**
```
Downloading embeddings model (very slow, taking >30 min)
Or: "Download interrupted" errors
```

**Cause:**
- Poor internet connection
- HuggingFace Hub site slow/congested
- Incomplete download

**Solution:**
1. Check internet: Test speed at speedtest.net (should be 10+ Mbps)
2. Wait it out (models large: 90+ MB)
3. If interrupted, delete cache:
   ```powershell
   Remove-Item "$env:USERPROFILE\.cache\huggingface" -Recurse -Force
   # Next run will re-download
   ```
4. Use smaller model (if available):
   ```env
   # Faster to download but lower quality:
   EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2  # (default, OK)
   ```

---

## 📞 Getting Help

### Check These First
1. ✅ Restart the application
2. ✅ Restart your computer
3. ✅ Verify all external dependencies (Ollama, etc.)
4. ✅ Check disk space (500+ MB free)
5. ✅ Check internet connection

### Where to Report Issues
- **GitHub Issues:** https://github.com/your-repo/local-ai-assistant/issues
- **Email Support:** your-support@email.com
- **Community Discord:** [if you have one]

### Provide When Reporting

**Always include:**
1. Your Windows version (Win10/11, build number)
2. Exact error message (copy + paste)
3. Steps to reproduce
4. Full app log output (if possible):
   ```
   C:\Users\{USERNAME}\AppData\Roaming\LocalAIAgent\data\logs\
   ```

**To collect logs:**
```powershell
# Run app and capture log:
.\LocalAIAgent.exe > app.log 2>&1

# Then attach app.log when reporting
```

---

## 📚 Quick Reference

| Issue | Quick Fix |
|-------|-----------|
| "Ollama unreachable" | Install Ollama from ollama.ai, start service |
| "DLL not found" | Install Visual C++ Redistributable (64-bit) |
| "Tesseract missing" | Download from GitHub, install to Program Files |
| "Email not working" | Provide credentials.json from Google Cloud |
| "Slow startup" | Normal first time (model download), check disk |
| "Can't edit .env" | Close app, right-click → Open with Notepad |
| "Antivirus blocks" | Add exception in Windows Defender |
| "Gmail auth failed" | Use app-specific password, not account password |
| "Out of memory" | Close other apps, reduce VECTOR_STORE_K in .env |
| "No documents loaded" | Add PDFs/images to data/documents/ folder |

---

## 🔗 Useful Links

- **Ollama:** https://ollama.ai
- **Tesseract-OCR:** https://github.com/UB-Mannheim/tesseract/wiki
- **Google OAuth Setup:** https://developers.google.com/workspace/guides/create-credentials
- **Visual C++ Runtime:** https://support.microsoft.com/en-us/help/2977003
- **VirusTotal:** https://www.virustotal.com
- **Project Repository:** https://github.com/your-repo/local-ai-assistant

---

**Last Updated:** April 2026  
**Still having issues?** Check the detailed guides:
- `WINDOWS_DEPLOYMENT_PLAN.md`
- `DEPLOYMENT_INSTRUCTIONS.md`

**Good luck! 🚀**
