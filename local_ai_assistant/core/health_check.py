import os
import sys
import subprocess
import shutil
from pathlib import Path
from configs.settings import settings, DATA_DIR

def run_health_check():
    """Verify system readiness with strict validation and self-healing."""
    print("\n" + "="*50)
    print(" SYSTEM PRODUCTION READINESS CHECK ")
    print("="*50)
    
    # Track critical vs optional failures
    critical_failed = False
    status_report = []

    # 1. LAYER 1: STRICT VALIDATION (FAIL FAST)
    # ----------------------------------------
    
    # A. User Context
    user_name = os.getenv("USER_NAME")
    if not user_name:
        status_report.append("[FAIL] Identity: USER_NAME missing in .env")
        critical_failed = True
    else:
        status_report.append(f"[OK] Identity: {user_name}")

    # B. Storage Writable
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        test_file = DATA_DIR / ".health_check"
        test_file.touch()
        test_file.unlink()
        status_report.append(f"[OK] Storage: Writable ({DATA_DIR})")
    except Exception as e:
        status_report.append(f"[FAIL] Storage: Access Denied ({e})")
        critical_failed = True

    # 2. LAYER 2 & 3: OLLAMA & MODELS (DEEP + SELF-HEAL)
    # -------------------------------------------------
    ollama_ready = False
    try:
        import ollama
        # Check connection
        try:
            models_list = ollama.list()
            status_report.append("[OK] Ollama: Service reachable")
            ollama_ready = True
        except Exception:
            status_report.append("[FAIL] Ollama: Service not running or unreachable")
            status_report.append("       -> Fix: Ensure Ollama is installed and running (ollama serve)")
            critical_failed = True
            
        if ollama_ready:
            # Check model existence
            model_name = settings.model_name
            try:
                # Modern library returns a ListModelsResponse object
                models = getattr(models_list, 'models', [])
                found = any(getattr(m, 'model', '').startswith(model_name) for m in models)
            except Exception:
                # Fallback for dict-style responses
                models = models_list.get('models', [])
                found = any(m.get('name', '').startswith(model_name) for m in models)
            
            if not found:
                print(f"[HEAL] Model missing. Attempting to pull '{model_name}'...")
                print("       (This may take several minutes depending on your internet speed)")
                try:
                    ollama.pull(model_name)
                    status_report.append(f"[OK] Model: '{model_name}' (Self-healed/Downloaded)")
                except Exception as e:
                    status_report.append(f"[FAIL] Model: '{model_name}' missing and pull failed ({e})")
                    critical_failed = True
            else:
                # Test call (Sanity check)
                try:
                    ollama.chat(model=model_name, messages=[{'role': 'user', 'content': 'hi'}])
                    status_report.append(f"[OK] Model: '{model_name}' (Functional)")
                except Exception as e:
                    status_report.append(f"[FAIL] Model: '{model_name}' crashed during test ({e})")
                    critical_failed = True
                    
    except ImportError:
        status_report.append("[FAIL] Ollama: Python library missing")
        critical_failed = True

    # 3. LAYER 2: EXTERNAL DEPENDENCIES (DEEP)
    # ----------------------------------------
    
    # A. Tesseract
    tesseract_path = shutil.which("tesseract")
    if not tesseract_path:
        # Check settings path
        if os.path.exists(settings.tesseract_cmd):
            tesseract_path = settings.tesseract_cmd
            
    if tesseract_path:
        try:
            res = subprocess.run([tesseract_path, "--version"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                status_report.append("[OK] Tesseract: Installed")
            else:
                status_report.append("[WARN] Tesseract: Installed but returned error")
        except Exception:
            status_report.append("[WARN] Tesseract: Binary unreachable")
    else:
        status_report.append("[WARN] Tesseract: Not found (OCR will be disabled)")

    # B. Poppler
    poppler_ready = any(shutil.which(tool) for tool in ["pdftoppm", "pdfinfo"])
    if poppler_ready:
        status_report.append("[OK] Poppler: Installed")
    else:
        status_report.append("[WARN] Poppler: Not found (PDF scanning disabled)")

    # C. Pipeline Syntax & Import Validation (Fail Fast)
    try:
        import agents.knowledge.retrieval_agent
        import engines.embedding_engine
        import engines.rag_engine
        import services.file_indexer_service
        status_report.append("[OK] Pipeline Integrity: Syntax and imports validated")
    except SyntaxError as e:
        status_report.append(f"[FAIL] Pipeline Integrity: SyntaxError in {e.filename} line {e.lineno}")
        status_report.append(f"       Details: {e.text.strip() if getattr(e, 'text', None) else ''}")
        critical_failed = True
    except ImportError as e:
        status_report.append(f"[FAIL] Pipeline Integrity: ImportError ({e})")
        critical_failed = True
    except Exception as e:
        status_report.append(f"[FAIL] Pipeline Integrity: Unexpected Error ({e})")
        critical_failed = True

    # D. Memory API Compatibility Validation
    try:
        from memory.conversation_memory import ConversationMemory
        mem = ConversationMemory()
        required_methods = [
            "add_pending_document",
            "get_pending_documents",
            "clear_pending_documents",
            "set_last_file",
            "get_last_file"
        ]
        missing = [m for m in required_methods if not hasattr(mem, m)]
        if missing:
            status_report.append(f"[FAIL] Memory Integrity: Missing APIs - {', '.join(missing)}")
            critical_failed = True
        else:
            status_report.append("[OK] Memory Integrity: APIs validated")
    except Exception as e:
        status_report.append(f"[FAIL] Memory Integrity: Could not load Memory ({e})")
        critical_failed = True

    # 4. FINAL OUTPUT
    # ---------------
    print("\n" + "\n".join(status_report))
    
    if critical_failed:
        print("\n" + "!"*50)
        print(" CRITICAL ERROR: STARTUP ABORTED ")
        print(" Please fix the [FAIL] items above and restart. ")
        print("!"*50 + "\n")
        return False
    else:
        print("\n" + "="*50)
        print(" [READY] System is stable and starting up. ")
        print("="*50 + "\n")
        return True
