"""
main.py
=======
Production entry point for the Local AI Assistant.
Redesigned for robust startup flow and visibility in packaged (.exe) environments.
"""

from __future__ import annotations

import os
import sys
import shutil
import threading
import time

def start_main_loop():
    """Guaranteed user interaction entry point."""
    print("\n>>> ENTERING MAIN LOOP <<<")
    print("\n=== AI Agent Ready ===")
    print("Type 'exit' to quit or 'status' for system health.\n")
    
    from pipelines.orchestrator import orchestrator
    from core.runtime_monitor import runtime_monitor
    from services.reminder_service import reminder_service
    from core.logging_config import get_logger
    log = get_logger(__name__)
    
    orchestrator.startup()

    while True:
        # Check LLM health before prompt
        if not runtime_monitor.is_feature_enabled("llm"):
            print("\n[!] WARNING: AI Engine (Ollama) is currently offline.")
            print("    Basic commands work, but AI queries will fail.")

        try:
            user_input = input("You: ").strip()

            if user_input.lower() in ["exit", "quit"]:
                print("Shutting down...")
                runtime_monitor.stop()
                reminder_service.stop()
                break

            if not user_input:
                continue

            _cmd = user_input.lower().strip(" .?!")

            # Special Commands
            if _cmd in ("system status", "status", "health check"):
                print(runtime_monitor.get_status_summary())
                continue

            if _cmd in ("what tools are available", "list tools"):
                from tools.tool_registry import tool_catalog
                print("Assistant:\n" + tool_catalog.describe_all())
                continue

            if _cmd in ("forget everything", "clear memory"):
                from memory.conversation_memory import conversation_memory as _mem
                _mem.clear()
                print("Assistant: Memory cleared.\n")
                continue

            # Process Query (Orchestrator already catches errors, but we check features first)
            if not runtime_monitor.is_feature_enabled("llm"):
                if _cmd not in ("exit", "status", "system status", "health check"):
                    print("Assistant: AI features are currently unavailable. Please check 'status'.\n")
                    continue

            # Runtime validation for Orchestrator method
            if not hasattr(orchestrator, "process_query"):
                log.error("[CRITICAL] Orchestrator missing process_query method. Falling back to run().")
                if hasattr(orchestrator, "run"):
                    response = orchestrator.run(user_input)
                else:
                    raise RuntimeError("Orchestrator has no executable method (run or process_query)")
            else:
                response = orchestrator.process_query(user_input)

            # Standard Output
            if response.bullets:
                print("Assistant:")
                for b in response.bullets:
                    print(f"  - {b}")
            else:
                print("Assistant:", response.answer)
            
            if response.source:
                print(f"  (Source: {response.source})")
            print()

        except Exception as e:
            print(f"[ERROR] Failed to process input: {e}")

def main():
    """Main startup sequence."""
    print("=== Starting AI Agent ===")
    
    # ── 1. Import Validation ────────────────────────────────────────────────
    try:
        from core.logging_config import setup_logging, get_logger
        from configs.settings import settings
        from core.runtime_paths import log_runtime_info
        from core.health_check import run_health_check
    except ImportError as e:
        print(f"CRITICAL: Missing core modules: {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    # ── 2. Logging & Runtime Info ───────────────────────────────────────────
    try:
        # Enable persistent logging
        _LOG_FILE = os.path.join(settings.data_dir, "logs", "agent.log")
        setup_logging(level=settings.log_level, log_format=settings.log_format, log_file=_LOG_FILE)
        log = get_logger(__name__)
        
        log_runtime_info()
    except Exception as e:
        print(f"CRITICAL: Runtime initialization failed: {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    # ── 3. Health Check Gatekeeper ──────────────────────────────────────────
    try:
        if not run_health_check():
            print("\nCRITICAL: Health check failed. System cannot start safely.")
            input("Press Enter to exit...")
            sys.exit(1)
    except Exception as e:
        print(f"CRITICAL: Health check crashed: {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    # ── 4. Boot Services (LIGHTWEIGHT — no heavy indexing) ──────────────────
    try:
        from services.reminder_service import reminder_service
        from core.runtime_monitor import runtime_monitor

        print("Starting services...")
        runtime_monitor.start()
        reminder_service.start()

        # NOTE: Vector store and document indexing are NOT started here.
        # They run on-demand when the user actually searches for files.
        # This keeps startup instant (< 3 seconds).
        log.info("Core services started (vector store deferred to on-demand).")


        # Background Email Poller
        def _email_poll_loop():
            from agents.knowledge.email_query_agent import invalidate_email_cache
            from agents.tasks.email_agent import EmailAgent
            while True:
                time.sleep(60)
                try:
                    invalidate_email_cache()
                    agent = EmailAgent()
                    new_emails = agent.fetch_recent_emails(last_n=settings.email_fetch_count)
                    if new_emails:
                        agent.save_to_cache(new_emails)
                except Exception:
                    pass

        threading.Thread(target=_email_poll_loop, daemon=True, name="email-poller").start()
        
    except Exception as e:
        print(f"CRITICAL: Service startup failed: {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    # ── 5. Start Main Loop ──────────────────────────────────────────────────
    start_main_loop()

if __name__ == "__main__":
    # Fix Python path
    _ROOT = os.path.dirname(os.path.abspath(__file__))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

    # Suppress noisy library output
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        main()
    except Exception as e:
        print(f"CRITICAL: Unhandled exception: {e}")
        input("Press Enter to exit...")
        sys.exit(1)
