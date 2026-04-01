# =============================================================================
# PROJECT CLEANUP SCRIPT
# Generated: 2026-04-01
# Run from repo root: .\cleanup.ps1
# Review every section before running. The script will NOT auto-execute —
# each section is wrapped in a function you call explicitly at the bottom.
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT     = "C:\Users\Sandeep\OneDrive\Documents\GitHub\Local_AI_Agent1.1"
$APP_ROOT = "$ROOT\local_ai_assistant"

# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────
function Remove-SafeFile($path) {
    if (Test-Path $path) {
        Remove-Item -Force $path
        Write-Host "  [DELETED] $path" -ForegroundColor Green
    } else {
        Write-Host "  [SKIP — not found] $path" -ForegroundColor Yellow
    }
}

function Remove-SafeDir($path) {
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
        Write-Host "  [DELETED DIR] $path" -ForegroundColor Green
    } else {
        Write-Host "  [SKIP — not found] $path" -ForegroundColor Yellow
    }
}

function Move-SafeFile($src, $dst) {
    if (Test-Path $src) {
        Move-Item -Path $src -Destination $dst -Force
        Write-Host "  [MOVED] $src  →  $dst" -ForegroundColor Cyan
    } else {
        Write-Host "  [SKIP — not found] $src" -ForegroundColor Yellow
    }
}

# =============================================================================
# STEP 0 — BACKUP (run this FIRST, before anything else)
# =============================================================================
function Step-Backup {
    Write-Host "`n=== STEP 0: BACKUP ===" -ForegroundColor Magenta

    Set-Location $ROOT

    # Option A: commit current state to git
    git add -A
    git commit -m "chore: snapshot before cleanup $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Write-Host "  [GIT] Committed current state." -ForegroundColor Green

    # Option B (alternative offline backup — uncomment if git is not available):
    # $backupDir = "$ROOT\..\Local_AI_Agent_backup_$(Get-Date -Format 'yyyyMMdd_HHmm')"
    # Copy-Item -Recurse -Path $ROOT -Destination $backupDir -Exclude ".git","venv311",".venv","__pycache__"
    # Write-Host "  [COPY] Backup saved to $backupDir" -ForegroundColor Green
}

# =============================================================================
# STEP 1 — DELETE: Temporary / Auto-Generated Files
# =============================================================================
function Step-DeleteTemp {
    Write-Host "`n=== STEP 1: DELETE Temporary / Auto-Generated Files ===" -ForegroundColor Magenta

    # 8.4 MB auto-generated file — regenerate anytime with generate_project_doc.py
    Remove-SafeFile "$ROOT\project_structure.txt"

    # Office temporary lock file — created when .docx is open, never belongs in repo
    Remove-SafeFile "$ROOT\~`$cal_AI_Assistant_Project_Report.docx"

    # Script run output artifact — not source code
    Remove-SafeFile "$APP_ROOT\scripts\quick_tests.out"
}

# =============================================================================
# STEP 2 — DELETE: One-Time Fix / Debug Scripts
# =============================================================================
function Step-DeleteFixScripts {
    Write-Host "`n=== STEP 2: DELETE One-Time Fix / Debug Scripts ===" -ForegroundColor Magenta

    # Prefixed with _ (throwaway convention), not imported anywhere, job done
    Remove-SafeFile "$APP_ROOT\_fix_orchestrator_0a.py"

    # 595 B one-time indentation fixer — not imported anywhere
    Remove-SafeFile "$APP_ROOT\fix_indent.py"

    # 1.7 KB one-time intent fix script — not imported anywhere
    Remove-SafeFile "$APP_ROOT\fix_intent.py"

    # Loose debug test at app root (not in tests/) — belongs with debug artifacts
    Remove-SafeFile "$APP_ROOT\test_email_context_debug.py"
}

# =============================================================================
# STEP 3 — DELETE: Duplicate / Outdated Documentation
# Notes:
#   - Canonical versions are in local_ai_assistant/docs/ and kept .md files
#   - Check the "kept" file exists before deleting the duplicate
# =============================================================================
function Step-DeleteDuplicateDocs {
    Write-Host "`n=== STEP 3: DELETE Duplicate / Outdated Documentation ===" -ForegroundColor Magenta

    # Superseded by CONTEXT_PROPAGATION_FIX_COMPLETE.md (in local_ai_assistant/)
    if (Test-Path "$APP_ROOT\CONTEXT_PROPAGATION_FIX_COMPLETE.md") {
        Remove-SafeFile "$ROOT\CONTEXT_FIX_QUICK_REFERENCE.md"
        Remove-SafeFile "$APP_ROOT\CONTEXT_PROPAGATION_FIX.md"        # draft; _COMPLETE is the final version
    } else {
        Write-Host "  [SKIP] CONTEXT_PROPAGATION_FIX_COMPLETE.md not found — skipping related removals" -ForegroundColor Yellow
    }

    # Superseded by local_ai_assistant/DRAFT_PERSISTENCE_FIX.md
    if (Test-Path "$APP_ROOT\DRAFT_PERSISTENCE_FIX.md") {
        Remove-SafeFile "$ROOT\DRAFT_PERSISTENCE_QUICK_FIX.md"
    }

    # Raw fix log — summarized version EMAIL_REPLY_FIXES_SUMMARY.md exists alongside it
    if (Test-Path "$ROOT\EMAIL_REPLY_FIXES_SUMMARY.md") {
        Remove-SafeFile "$ROOT\EMAIL_REPLY_FIXES.md"
    }

    # Implementation notes (19.5 KB) — code is built; duplicates docs/email_draft_send_system.md
    if (Test-Path "$APP_ROOT\docs\email_draft_send_system.md") {
        Remove-SafeFile "$ROOT\EMAIL_REPLY_IMPLEMENTATION_GUIDE.md"
        Remove-SafeFile "$ROOT\EMAIL_REPLY_ARCHITECTURE.md"
    }

    # Root-level duplicate of local_ai_assistant/SEMANTIC_EMAIL_SEARCH_GUIDE.md
    if (Test-Path "$APP_ROOT\SEMANTIC_EMAIL_SEARCH_GUIDE.md") {
        Remove-SafeFile "$ROOT\SEMANTIC_EMAIL_SEARCH_README.md"
    }

    # Phase checklist — PHASE3_COMPLETE.md confirms all items done
    if (Test-Path "$APP_ROOT\PHASE3_COMPLETE.md") {
        Remove-SafeFile "$APP_ROOT\COMPLETION_CHECKLIST.md"
    }

    # Phase sign-off doc — useful during dev, superseded by actual code
    Remove-SafeFile "$APP_ROOT\PHASE3_COMPLETE.md"

    # Empty 40-byte stub README — either populate or remove
    $readmeContent = if (Test-Path "$APP_ROOT\README.md") { (Get-Item "$APP_ROOT\README.md").Length } else { -1 }
    if ($readmeContent -ge 0 -and $readmeContent -le 100) {
        Remove-SafeFile "$APP_ROOT\README.md"
        Write-Host "  [NOTE] Consider creating a proper README.md for local_ai_assistant/" -ForegroundColor Yellow
    } else {
        Write-Host "  [SKIP] README.md has content ($readmeContent bytes) — leaving it" -ForegroundColor Yellow
    }
}

# =============================================================================
# STEP 4 — MOVE: Loose Test Files → tests/
# These are test files living at local_ai_assistant/ root instead of tests/
# =============================================================================
function Step-MoveTests {
    Write-Host "`n=== STEP 4: MOVE Loose Test Files to tests/ ===" -ForegroundColor Magenta

    $testsDir = "$APP_ROOT\tests"

    # Covered by scripts/test_draft_flow.py and the tests/ suite
    Move-SafeFile "$APP_ROOT\test_draft_persistence.py"    "$testsDir\test_draft_persistence.py"

    # 335 B import sanity check — useful as a regression test in the suite
    Move-SafeFile "$APP_ROOT\test_imports.py"              "$testsDir\test_imports.py"

    # Covered by tests/test_phase3_intent.py but keep as named test
    Move-SafeFile "$APP_ROOT\test_intent_classifier.py"    "$testsDir\test_intent_classifier.py"

    # Simpler duplicate of test_intent_classifier; move so it is at least findable
    Move-SafeFile "$APP_ROOT\test_intent_simple.py"        "$testsDir\test_intent_simple.py"
}

# =============================================================================
# STEP 5 — DELETE: Empty / Accidental Data Files
# =============================================================================
function Step-DeleteEmptyFiles {
    Write-Host "`n=== STEP 5: DELETE Empty / Accidental Data Files ===" -ForegroundColor Magenta

    # 0 bytes — not written to by active code
    Remove-SafeFile "$APP_ROOT\data\emails.json"

    # 0-byte duplicates — real data lives at data/memory.json and data/reminders.json
    Remove-SafeFile "$APP_ROOT\data\documents\memory.json"
    Remove-SafeFile "$APP_ROOT\data\documents\reminders.json"

    # data/ folder is not a Python package — __init__.py has no purpose here
    Remove-SafeFile "$APP_ROOT\data\documents\__init__.py"

    # 0-byte accidental placeholder — not a directory
    Remove-SafeFile "$APP_ROOT\ui\desktop"
}

# =============================================================================
# STEP 6 — DELETE: Typo Folder (serviecs → services already exists)
# =============================================================================
function Step-DeleteTypoFolder {
    Write-Host "`n=== STEP 6: DELETE Typo Folder 'serviecs/' ===" -ForegroundColor Magenta

    # Confirm the real services/ folder exists before removing the typo copy
    if (Test-Path "$APP_ROOT\services") {
        Remove-SafeDir "$APP_ROOT\serviecs"
    } else {
        Write-Host "  [ABORT] Real services/ folder not found — skipping for safety" -ForegroundColor Red
    }
}

# =============================================================================
# STEP 7 — UPDATE: Migrate scripts/test_email_reply.py from v1 → v2 API
# Then STEP 8 removes the v1 file.
#
# API change summary:
#   v1: generate_email_reply(email_id: str, tone="professional", ...)
#   v2: generate_email_reply(email: dict, tone="professional", ...)
#       (find the email dict manually via load_all_emails by id)
#
# The file also has a print statement referencing the old import path — fix that too.
# =============================================================================
function Step-UpdateTestEmailReply {
    Write-Host "`n=== STEP 7: UPDATE scripts/test_email_reply.py to use v2 API ===" -ForegroundColor Magenta

    $file = "$ROOT\scripts\test_email_reply.py"

    if (-not (Test-Path $file)) {
        Write-Host "  [SKIP] $file not found" -ForegroundColor Yellow
        return
    }

    $content = Get-Content $file -Raw

    # ── Change 1: import in test_imports_work() (line ~59) ──────────────────
    $old1 = @'
        from agents.knowledge.email_reply_agent import generate_email_reply
        print("✓ agents.knowledge.email_reply_agent")
'@
    $new1 = @'
        from agents.knowledge.email_reply_agent_v2 import generate_email_reply
        print("✓ agents.knowledge.email_reply_agent_v2")
'@

    # ── Change 2: import in test_reply_generation() (line ~150) ─────────────
    $old2 = @'
        from agents.knowledge.email_reply_agent import (
            generate_email_reply,
            get_tone_options,
        )
        from agents.knowledge.email_query_agent import load_all_emails

        emails = load_all_emails()
        if not emails:
            print("✗ No emails to test with")
            return False

        # Test each tone
        tones = list(get_tone_options().keys())
        email_id = str(emails[0]["id"])
        
        print(f"Testing reply generation for email: {email_id}")
        print(f"Available tones: {', '.join(tones)}")

        for tone in tones:
            print(f"\n  Testing tone: {tone}...")
            reply = generate_email_reply(email_id, tone=tone)
'@
    $new2 = @'
        from agents.knowledge.email_reply_agent_v2 import (
            generate_email_reply,
            get_tone_options,
        )
        from agents.knowledge.email_query_agent import load_all_emails

        emails = load_all_emails()
        if not emails:
            print("✗ No emails to test with")
            return False

        # Test each tone (v2 API takes an email dict, not an id string)
        tones = list(get_tone_options().keys())
        email = emails[0]  # v2 expects a dict

        print(f"Testing reply generation for email id: {email.get('id')}")
        print(f"Available tones: {', '.join(tones)}")

        for tone in tones:
            print(f"\n  Testing tone: {tone}...")
            reply = generate_email_reply(email, tone=tone)
'@

    # ── Change 3: fix print/help text at end of file (line ~370) ────────────
    $old3 = 'from agents.knowledge.email_reply_agent import generate_email_reply; print(generate_email_reply(''1'', tone=''professional''))'
    $new3 = 'from agents.knowledge.email_reply_agent_v2 import generate_email_reply; from agents.knowledge.email_query_agent import load_all_emails; emails = load_all_emails(); print(generate_email_reply(emails[0], tone=''professional'')) if emails else print(\"No emails\")'

    # Apply all three replacements
    $updated = $content.Replace($old1, $new1).Replace($old2, $new2).Replace($old3, $new3)

    if ($updated -eq $content) {
        Write-Host "  [WARN] No replacements matched — file may have already been updated or differs from expected." -ForegroundColor Yellow
        Write-Host "         Review the file manually before proceeding to Step 8." -ForegroundColor Yellow
    } else {
        Set-Content -Path $file -Value $updated -NoNewline
        Write-Host "  [UPDATED] $file" -ForegroundColor Green
        Write-Host "  Verify the changes look correct before running Step 8." -ForegroundColor Yellow
    }
}

# =============================================================================
# STEP 8 — DELETE: email_reply_agent.py v1
# Run ONLY after Step 7 has been applied and verified.
# =============================================================================
function Step-DeleteV1Agent {
    Write-Host "`n=== STEP 8: DELETE email_reply_agent.py (v1) ===" -ForegroundColor Magenta

    # Safety check: confirm v2 exists
    if (-not (Test-Path "$APP_ROOT\agents\knowledge\email_reply_agent_v2.py")) {
        Write-Host "  [ABORT] v2 file not found — aborting for safety" -ForegroundColor Red
        return
    }

    # Safety check: confirm no remaining imports of v1 (excluding v2 file itself and git history)
    Write-Host "  Scanning for remaining v1 imports..." -ForegroundColor Gray
    $remaining = Select-String -Path "$APP_ROOT\**\*.py" -Pattern "from agents\.knowledge\.email_reply_agent import" -Recurse `
        | Where-Object { $_.Path -notlike "*email_reply_agent_v2*" }

    if ($remaining) {
        Write-Host "  [ABORT] The following files still import v1 — fix them first:" -ForegroundColor Red
        $remaining | ForEach-Object { Write-Host "    $($_.Path):$($_.LineNumber)" -ForegroundColor Red }
    } else {
        Remove-SafeFile "$APP_ROOT\agents\knowledge\email_reply_agent.py"
        Write-Host "  [DONE] v1 agent removed." -ForegroundColor Green
    }
}

# =============================================================================
# EXECUTION ORDER
# Uncomment steps one at a time. Review results before moving to the next step.
# =============================================================================

Write-Host @"

  CLEANUP SCRIPT LOADED
  ─────────────────────
  Functions available:
    Step-Backup             # ALWAYS run first
    Step-DeleteTemp         # Safe — auto-generated files
    Step-DeleteFixScripts   # Safe — one-time fix scripts
    Step-DeleteDuplicateDocs # Review before running
    Step-MoveTests          # Moves files, doesn't delete
    Step-DeleteEmptyFiles   # Safe — all 0-byte files
    Step-DeleteTypoFolder   # Safe — empty folder
    Step-UpdateTestEmailReply  # Review diff carefully
    Step-DeleteV1Agent      # Run AFTER Step-UpdateTestEmailReply is verified

  Recommended execution:
    1.  Step-Backup
    2.  Step-DeleteTemp
    3.  Step-DeleteFixScripts
    4.  Step-DeleteEmptyFiles
    5.  Step-DeleteTypoFolder
    6.  Step-MoveTests
    7.  Step-DeleteDuplicateDocs
    8.  Step-UpdateTestEmailReply   ← then manually review the diff
    9.  Step-DeleteV1Agent          ← only after step 8 is verified

  Run each function individually, e.g.:
    Step-Backup

"@ -ForegroundColor White
