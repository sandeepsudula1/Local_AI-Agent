import sys
import os
import time
import shutil
from datetime import datetime, timedelta

# ================================
# AUTO-CLEAR __pycache__ on every startup
# This ensures code changes take effect immediately without a manual cache clear.
# ================================
_ROOT_DIR_TMP = os.path.dirname(os.path.abspath(__file__))
for _dp, _dirs, _ in os.walk(_ROOT_DIR_TMP):
    for _d in _dirs:
        if _d == "__pycache__":
            try:
                shutil.rmtree(os.path.join(_dp, _d))
            except Exception:
                pass

# ================================
# FIX PYTHON PATH
# ================================
ROOT_DIR = _ROOT_DIR_TMP
sys.path.append(ROOT_DIR)

# ================================
# SUPPRESS noisy library warnings before any imports
# ================================
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

import logging as _logging
_logging.getLogger("transformers").setLevel(_logging.ERROR)
_logging.getLogger("sentence_transformers").setLevel(_logging.ERROR)
_logging.getLogger("hf_xai_core").setLevel(_logging.ERROR)
_logging.getLogger("mlx").setLevel(_logging.CRITICAL)

# ================================
# AUTO-RELAUNCH with venv311 if running with wrong Python
# Uses subprocess.run so the terminal stays attached (os.execv breaks on Windows)
# ================================
try:
    import imapclient as _imap_check
except ImportError:
    import subprocess as _sp
    _venv_python = os.path.join(_ROOT_DIR_TMP, "venv311", "Scripts", "python.exe")
    if os.path.exists(_venv_python):
        _result = _sp.run([_venv_python] + sys.argv)
        sys.exit(_result.returncode)
    else:
        print("ERROR: venv311\\Scripts\\python.exe not found.")
        sys.exit(1)

# ================================
# IMPORT AGENTS
# ================================
from agents.core.planner_agent import decide_intent
from agents.core.general_agent import handle_general

# Reminder Agent
from agents.tasks.reminder_agent import (
    extract_reminder_details,
    add_reminder,
    list_reminders,
    delete_reminder
)

# Email Agents
from agents.knowledge.email_query_agent import search_emails_by_text, improved_search_emails
from agents.knowledge.email_summarizer_agent import handle_email_summary, summarize_emails_by_query
from agents.tasks.email_agent import EmailAgent   # For IMAP fetch

# Document Agents
from agents.knowledge.retrieval_agent import handle_retrieval
from agents.knowledge.summary_agent import handle_summary
from agents.knowledge.topic_agent import handle_topics
from agents.knowledge.document_list_agent import list_all_documents

# LangChain Document Processing
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
import threading
import itertools
import shutil
import sqlite3

import pytesseract
import re
from PIL import Image
import pandas as pd

# ── Optional: MCP bridge (graceful no-op if mcp package not installed) ─────
# When the 'mcp' package is available this enables bridged tool dispatch and
# the --mcp / --mcp-sse CLI flags.  Nothing changes if the package is absent.
try:
    from agent_mcp.bridge import MCPBridge as _MCPBridge
    _MCP_BRIDGE = _MCPBridge()
    _MCP_ENABLED = True
except Exception:
    _MCP_BRIDGE = None
    _MCP_ENABLED = False


def _collapse_repeated_lines(text, max_repeats=2):
    """Collapse consecutive repeated lines to at most `max_repeats` occurrences."""
    if not text:
        return text
    lines = text.splitlines()
    out = []
    prev = None
    count = 0
    for L in lines:
        if L == prev:
            count += 1
            if count <= max_repeats:
                out.append(L)
        else:
            prev = L
            count = 1
            out.append(L)
    return "\n".join(out)


def _summarize_snippet(text, max_paragraphs=3):
    """Collapse repeated lines, then keep up to `max_paragraphs` unique paragraphs."""
    if not text:
        return text
    cleaned = _collapse_repeated_lines(text)
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    seen = set()
    out = []
    for p in paragraphs:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= max_paragraphs:
            break
    return "\n\n".join(out)

# ================================
# CONFIG
# ================================
THRESHOLD = 1.5
MODEL_NAME = "llama3.2:1b"

DOCS_PATH = os.path.join("data", "documents")
# Use a versioned path so any leftover incompatible Chroma SQLite files
# (which Windows may keep locked) are automatically bypassed.
VECTOR_STORE_PATH = os.path.join("data", "vector_store_v2")

pytesseract.pytesseract.tesseract_cmd = r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"

# ── MCP server mode ─────────────────────────────────────────────────────────
# Pass --mcp    → stdio transport  (Claude Desktop / VS Code MCP extensions)
# Pass --mcp-sse → SSE transport   (HTTP, port 8765, multi-client / remote)
# The interactive CLI loop is completely unaffected when neither flag is given.
if "--mcp" in sys.argv or "--mcp-sse" in sys.argv:
    from agent_mcp.server import main as _mcp_main
    _transport = "sse" if "--mcp-sse" in sys.argv else "stdio"
    print(f"[MCP] Starting server — transport: {_transport}")
    _mcp_main(transport=_transport)
    sys.exit(0)

# ================================
# LOAD DOCUMENTS
# ================================
documents = []

if os.path.exists(DOCS_PATH):
    for file in os.listdir(DOCS_PATH):
        full_path = os.path.join(DOCS_PATH, file)
        try:
            if file.endswith(".pdf"):
                loader = PyPDFLoader(full_path)
                for doc in loader.load():
                    doc.metadata["source"] = file
                    documents.append(doc)

            elif file.endswith(".csv"):
                df = pd.read_csv(full_path)
                for _, row in df.iterrows():
                    row_text = ", ".join([f"{col}: {row[col]}" for col in df.columns])
                    documents.append(Document(page_content=row_text, metadata={"source": file}))

            elif file.endswith((".png", ".jpg", ".jpeg")):
                try:
                    extracted = pytesseract.image_to_string(Image.open(full_path)).strip()
                    if extracted:
                        documents.append(Document(page_content=extracted, metadata={"source": file}))
                    else:
                        print(f"[Info] No OCR text extracted from {file}; image skipped from index.")
                except Exception as _ocr_err:
                    print(f"[Warning] OCR failed for {file}: {_ocr_err} — image skipped from index.")
        except Exception as _load_err:
            print(f"[Warning] Could not load {file}: {_load_err}")

print(f"Loaded {len(documents)} document(s).")

# ================================
# VECTOR DB
# ================================
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Globals for lazy-loading
vector_db = None
vector_ready = False
vector_loading = False
vector_progress = 0


def _print_progress(msg: str):
    pass  # internal messages suppressed — only show key status to user


def _docs_newer_than_vector_store() -> bool:
    """Return True if any document was modified after the persisted vector store was built."""
    db_file = os.path.join(VECTOR_STORE_PATH, "chroma.sqlite3")
    if not os.path.exists(db_file):
        return True
    store_mtime = os.path.getmtime(db_file)
    if not os.path.exists(DOCS_PATH):
        return False
    for fname in os.listdir(DOCS_PATH):
        fpath = os.path.join(DOCS_PATH, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) > store_mtime:
            return True
    return False


def load_vector_store_background():
    global vector_db, vector_ready, vector_loading, vector_progress
    vector_loading = True
    try:
        vector_progress = 5
        _print_progress("Starting vector store load")

        emb = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"}
        )
        vector_progress = 15

        # Force rebuild when any document is newer than the persisted vector store
        if _docs_newer_than_vector_store() and os.path.exists(VECTOR_STORE_PATH):
            print("[Info] Documents changed — rebuilding vector store from scratch...")
            try:
                shutil.rmtree(VECTOR_STORE_PATH)
            except Exception as _rm_err:
                print(f"[Warning] Could not remove old vector store: {_rm_err}")

        # If persisted store exists, try loading
        if os.path.exists(VECTOR_STORE_PATH) and any(os.scandir(VECTOR_STORE_PATH)):
            _print_progress("Found persisted vector store; attempting to load...")
            vector_progress = 30
            try:
                vector_db = Chroma(persist_directory=VECTOR_STORE_PATH, embedding_function=emb)
                vector_progress = 100
                _print_progress("Vector store loaded from disk (ready).")
                vector_ready = True
                return
            except Exception as e:
                _print_progress(f"Failed to load persisted store: {e}; will rebuild.")
                # If the persisted Chroma DB has an incompatible schema, remove it
                # so that we can rebuild a fresh store. This avoids sqlite schema
                # errors during `from_documents` when reusing the same directory.
                try:
                    if os.path.exists(VECTOR_STORE_PATH):
                        _print_progress("Removing existing persisted vector store files...")
                        shutil.rmtree(VECTOR_STORE_PATH)
                        os.makedirs(VECTOR_STORE_PATH, exist_ok=True)
                        _print_progress("Persist directory reset.")
                except Exception as ex_rm:
                    _print_progress(f"Failed to reset persist dir: {ex_rm}")

        # Build embeddings and vector DB
        _print_progress("No valid persisted store found; building embeddings from documents...")
        vector_progress = 40
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=80)
        chunks = splitter.split_documents(documents)
        vector_progress = 60

        # Create the Chroma DB (this may take time)
        try:
            vector_db = Chroma.from_documents(chunks, emb, persist_directory=VECTOR_STORE_PATH)
        except sqlite3.OperationalError as sqe:
            _print_progress(f"Chroma OperationalError during from_documents: {sqe}; attempting to reset persist dir and retry.")
            try:
                if os.path.exists(VECTOR_STORE_PATH):
                    shutil.rmtree(VECTOR_STORE_PATH)
                os.makedirs(VECTOR_STORE_PATH, exist_ok=True)
                _print_progress("Persist directory reset; retrying vector build...")
                vector_db = Chroma.from_documents(chunks, emb, persist_directory=VECTOR_STORE_PATH)
            except Exception as retry_ex:
                _print_progress(f"Retry_failed: {retry_ex}")
                # leave vector_db as None so system falls back to non-local answers
                vector_db = None
        except Exception as e:
            _print_progress(f"Failed building Chroma DB: {e}")
            vector_db = None
        vector_progress = 90
        _print_progress("Persisting vector store to disk...")
        try:
            vector_db.persist()
        except Exception:
            pass

        vector_progress = 100
        vector_ready = True
        print("[Ready] Knowledge base built.")

    finally:
        vector_loading = False


# Start background loader thread
loader_thread = threading.Thread(target=load_vector_store_background, daemon=True)
loader_thread.start()

# ================================
# REMINDER BACKGROUND POLLING
# ================================
def _reminder_poll_loop():
    """Poll reminders.json every 5 s and fire notifications for due reminders."""
    from agents.tasks.notification_agent import notify as _do_notify
    import json as _json
    import dateparser as _dp

    _CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    _REM_FILE = os.path.join(_CURRENT_DIR, "data", "reminders.json")

    def _load():
        if os.path.exists(_REM_FILE):
            try:
                with open(_REM_FILE, "r") as _f:
                    return _json.load(_f)
            except Exception:
                return []
        return []

    def _save(rems):
        with open(_REM_FILE, "w") as _f:
            _json.dump(rems, _f, indent=4)

    while True:
        try:
            rems = _load()
            changed = False
            now = datetime.now()
            for r in rems:
                if r.get("fired"):
                    continue
                t = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        t = datetime.strptime(r["time"], fmt)
                        break
                    except Exception:
                        pass
                if not t:
                    try:
                        t = _dp.parse(r["time"])
                    except Exception:
                        pass
                if not t:
                    continue
                diff = (now - t).total_seconds()
                if 0 <= diff <= 60:   # within 60-second window
                    txt = r.get("text", "Reminder")
                    print(f"\n🔔 Reminder: {txt}\n", flush=True)
                    _do_notify("Reminder", txt)
                    r["fired"] = True
                    changed = True
            if changed:
                _save(rems)
        except Exception:
            pass
        time.sleep(5)

_reminder_thread = threading.Thread(target=_reminder_poll_loop, daemon=True)
_reminder_thread.start()

print("\nSmart AI Multi-Agent System Ready.\n")
print("Examples: \n- Which is better, Python or Java?\n- Compare 'Node.js' vs 'Deno'\n")

# ================================
# SYSTEM QUERIES
# ================================
def handle_system_query(user_input):
    text = user_input.lower()

    if "today" in text and "date" in text:
        return f"Today's date is {datetime.now().strftime('%A, %d %B %Y')}"

    if "tomorrow" in text:
        nxt = datetime.now() + timedelta(days=1)
        return f"Tomorrow's date is {nxt.strftime('%A, %d %B %Y')}"

    return None


def _to_bullets(text: str, max_bullets: int = 5):
    if not text:
        return []
    # Split into simple sentences
    parts = [p.strip() for p in re.split(r'[\n\r]+|(?<=[\.[\?\!])\s+', text) if p.strip()]
    bullets = []
    seen_lower: set = set()
    for p in parts:
        if len(bullets) >= max_bullets:
            break
        # short clean-up and deduplication
        s = p.replace('\n', ' ').strip()
        s_key = s.lower()
        if s and s_key not in seen_lower:
            seen_lower.add(s_key)
            bullets.append(s)
    return bullets


# ================================
# COMPARISON HANDLER
# ================================
def handle_compare(user_input: str):
    # Improved parsing for comparison items. Supports quoted items, multi-word names,
    # 'compare X and Y', 'X vs Y', 'X versus Y', 'which is better X or Y', 'compare X with Y',
    # and simple 'X or Y' heuristics.
    def clean_item(s: str):
        s = s.strip()
        # strip surrounding quotes and punctuation
        s = re.sub(r'^["\'\(\[\{]+', '', s)
        s = re.sub(r'["\'\)\]\},\.\?\!]+$', '', s)
        return s.strip()

    def parse_items(text: str):
        t = text.strip()

        # 1) quoted pairs: "A" vs "B" or 'A' vs 'B'
        m = re.search(r"[\"']([^\"']+)[\"']\s*(?:vs\.?|versus|or|,)\s*[\"']([^\"']+)[\"']", t, flags=re.I)
        if m:
            return clean_item(m.group(1)), clean_item(m.group(2))

        # 2) compare X and Y / compare X with Y / compare X to Y
        m = re.search(r"compare\s+(.+?)\s+(?:and|with|to)\s+(.+)$", t, flags=re.I)
        if m:
            return clean_item(m.group(1)), clean_item(m.group(2))

        # 3) X vs Y, X versus Y
        m = re.search(r"(.+?)\s+vs\.?\s+(.+?)$", t, flags=re.I)
        if m:
            return clean_item(m.group(1)), clean_item(m.group(2))
        m = re.search(r"(.+?)\s+versus\s+(.+?)$", t, flags=re.I)
        if m:
            return clean_item(m.group(1)), clean_item(m.group(2))

        # 4) Which is better X or Y
        m = re.search(r"which is better[:,]?\s*(.+?)\s+or\s+(.+?)\??$", t, flags=re.I)
        if m:
            return clean_item(m.group(1)), clean_item(m.group(2))

        # 5) simple 'X or Y' at end of sentence: take last two comma/space-separated chunks
        m = re.search(r"(?:\b|\s)([\w\-\.#\+]+(?:[\s\w\-\.#\+]+)?)\s+or\s+([\w\-\.#\+]+(?:[\s\w\-\.#\+]+)?)\??$", t, flags=re.I)
        if m:
            return clean_item(m.group(1)), clean_item(m.group(2))

        return None, None

    a, b = parse_items(user_input)

    # If parsed, run retrieval for each to gather facts
    facts = []
    sources = []
    if a:
        ans_a, src_a = handle_retrieval(a, vector_db, THRESHOLD, MODEL_NAME)
        if ans_a:
            facts.append(f"Facts about {a}: {ans_a}")
        if src_a:
            sources.append(f"{a}: {src_a}")
    if b:
        ans_b, src_b = handle_retrieval(b, vector_db, THRESHOLD, MODEL_NAME)
        if ans_b:
            facts.append(f"Facts about {b}: {ans_b}")
        if src_b:
            sources.append(f"{b}: {src_b}")

    # Build a structured synthesis prompt emphasizing local facts and concise output.
    prompt_lines = [
        "You are an assistant that provides concise, well-structured comparisons using only the provided context facts. Do not invent facts.",
        f"User question: {user_input}",
        "",
    ]

    if facts:
        prompt_lines.append("Context facts (from local documents):")
        for f in facts:
            # present as bullet points
            prompt_lines.append(f"- {f}")
        prompt_lines.append("")

    if sources:
        prompt_lines.append("Sources:")
        for s in sources:
            prompt_lines.append(f"- {s}")
        prompt_lines.append("")

    prompt_lines.extend([
        "Required format (use Markdown):",
        "# Short answer (1 sentence)",
        "## Pros\n- (bullet points)",
        "## Cons\n- (bullet points)",
        "**Recommendation:** (1 sentence)",
        "\nReturn only the Markdown-formatted comparison. Do not add any extra commentary.",
    ])

    prompt = "\n".join(prompt_lines)

    return handle_general(prompt, MODEL_NAME, temperature=0.0)

# ================================
# MERGED EMAIL CACHE (OPTION B)
# ================================
def load_all_emails():
    """
    Load emails.json + email_cache.json (merged)
    """
    emails = []

    # emails.json (manual)
    email_json = "data/emails.json"
    if os.path.exists(email_json):
        try:
            emails.extend(json.load(open(email_json)))
        except:
            pass

    # email_cache.json (IMAP fetched)
    email_cache = "data/email_cache.json"
    if os.path.exists(email_cache):
        try:
            cache = json.load(open(email_cache))
            emails.extend(cache.get("emails", []))
        except:
            pass

    return emails


# ======================================
# EMAIL AUTO-FETCH HELPER
# ======================================
_last_email_fetch_time = 0
_EMAIL_FETCH_COOLDOWN = 30  # seconds — short enough to catch new emails quickly


def _auto_fetch_emails(force: bool = False):
    """Fetch all recent emails from IMAP and merge into cache (deduped).

    Always runs when force=True. Otherwise respects a short cooldown to
    avoid hammering the IMAP server on every keystroke.
    """
    global _last_email_fetch_time
    now_ts = time.time()
    if not force and (now_ts - _last_email_fetch_time) < _EMAIL_FETCH_COOLDOWN:
        return  # too soon — already fresh
    try:
        agent = EmailAgent()
        if hasattr(agent, 'fetch_recent_emails'):
            new_emails = agent.fetch_recent_emails(last_n=200)
        else:
            new_emails = agent.fetch_unread_emails()
        if new_emails:
            result = agent.save_to_cache(new_emails)
            # Only log when new emails were actually added
            if result and "Saved 0" not in result:
                print(f"[Email] {result}")
        _last_email_fetch_time = now_ts
    except Exception as _fetch_err:
        print(f"[Email] IMAP fetch failed: {_fetch_err}")
        _last_email_fetch_time = now_ts


# =====================================================
# BACKGROUND EMAIL POLLING THREAD
# Automatically fetches new emails every 60 seconds while agent runs.
# This means a new email appears in the cache within 1 minute — no
# action needed from the user.
# =====================================================
def _email_poll_loop():
    """Daemon thread: refresh inbox from IMAP every 60 seconds."""
    from agents.knowledge.email_query_agent import invalidate_email_cache
    while True:
        time.sleep(60)
        try:
            invalidate_email_cache()
            _auto_fetch_emails(force=True)
        except Exception:
            pass

_email_poll_thread = threading.Thread(target=_email_poll_loop, daemon=True)
_email_poll_thread.start()

# =====================================================
# MAIN LOOP
# =====================================================
# Fetch emails synchronously at startup so the cache is fresh before
# the user's first query.
print("[Email] Syncing inbox...")
try:
    _auto_fetch_emails(force=True)
except Exception:
    pass

while True:
    user_input = input("You: ").strip()

    if not user_input:
        continue

    if user_input.lower() == "exit":
        print("Assistant shutting down…")
        break

    # SYSTEM QUICK CHECK
    sys_out = handle_system_query(user_input)
    if sys_out:
        print("Assistant:", sys_out, "\n")
        continue

    # Short conversational responses should never trigger document lookup.
    # Route them straight to the LLM without scanning docs.
    _CONVERSATIONAL = {"yes", "no", "ok", "okay", "sure", "thanks",
                       "thank you", "alright", "nope", "yep", "yup",
                       "nah", "bye", "got it", "cool", "nice"}
    if user_input.lower().strip() in _CONVERSATIONAL:
        print("Assistant:", handle_general(user_input, MODEL_NAME), "\n")
        continue

    # WHAT USER WANTS?
    intent = decide_intent(user_input)
    print("Planner Decision:", intent)

    # ======================================
    # CHAT — pure conversation, no doc lookup
    # ======================================
    if intent == "CHAT":
        print("Assistant:", handle_general(user_input, MODEL_NAME), "\n")
        continue

    # ======================================
    # GENERAL — try docs first, then LLM
    # ======================================
    if intent == "GENERAL":
        # Check if it looks even slightly document-related by trying vector search.
        # If vector DB has a good answer (query tokens appear in result), show it.
        # Otherwise, respond with the LLM directly — no noisy in-memory token scan.
        answered = False
        if vector_ready and vector_db is not None:
            try:
                ans, src = handle_retrieval(user_input, vector_db, THRESHOLD, MODEL_NAME)
                if ans:
                    q_tokens = [t for t in user_input.lower().split()
                                if len(t) > 3 and t not in {
                                    "what","this","that","with","have","from",
                                    "will","your","more","about","tell","give",
                                    "make","show","does","which","when","where",
                                    "how","are","you","the","and","not","but",
                                    "can","all","its","was","had","has","did"}]
                    ans_lower = (ans or "").lower()
                    # Only surface doc answer if the result is actually relevant
                    if q_tokens and any(tok in ans_lower for tok in q_tokens):
                        bullets = _to_bullets(_summarize_snippet(ans, max_paragraphs=3), max_bullets=5)
                        print("Assistant:")
                        for b in bullets:
                            print(f"  - {b}")
                        if src:
                            print(f"  (Source: {src})")
                        print()
                        answered = True
            except Exception:
                pass

        if not answered:
            # Pure LLM response — handles conversational and general knowledge questions
            print("Assistant:", handle_general(user_input, MODEL_NAME), "\n")
        continue

    # ======================================
    # TIME & DATE
    # ======================================
    if intent == "TIME":
        print("Assistant:", datetime.now().strftime("%H:%M:%S"), "\n")
        continue

    if intent == "DATE":
        print("Assistant:", datetime.now().strftime("%A, %d %B %Y"), "\n")
        continue

    # ======================================
    # GREETING
    # ======================================
    if intent == "GREETING":
        print("Assistant: Hello! How can I help you today?\n")
        continue

    # ======================================
    # REMINDERS
    # ======================================
    if intent == "REMINDER_SET":
        text, rtime = extract_reminder_details(user_input)
        if not rtime:
            print("Assistant: I could not understand the reminder time. Try 'remind me at 15:22' or 'remind me in 10 minutes'.\n")
            continue

        # Ask for confirmation before saving
        print(f"Assistant: I parsed this reminder as:\n  - Message: '{text}'\n  - Time: {rtime}\nDo you want to save it? (yes/no)")
        conf = input("You: ").strip().lower()
        if conf in ("y", "yes"):
            print("Assistant:", add_reminder(text, rtime), "\n")
        else:
            print("Assistant: Reminder canceled.\n")
        continue

    if intent == "REMINDER_LIST":
        print("Assistant:", list_reminders(), "\n")
        continue

    if intent == "REMINDER_DELETE":
        print("Assistant: Which reminder should I delete?")
        to_delete = input("You: ")
        print("Assistant:", delete_reminder(to_delete), "\n")
        continue

    # ======================================
    # EMAIL SUMMARY
    # ======================================
    if intent == "EMAIL_SUMMARY":
        # Force-invalidate the in-memory email cache so load_all_emails() hits IMAP fresh
        try:
            from agents.knowledge.email_query_agent import invalidate_email_cache
            invalidate_email_cache()
        except Exception:
            pass
        print("Assistant: Fetching latest emails...\n")
        print(handle_email_summary())
        continue

    # ======================================
    # EMAIL SEARCH (NATURAL LANGUAGE)
    # ======================================
    if intent == "EMAIL_SEARCH":
        # Force-invalidate the in-memory email cache so load_all_emails() hits IMAP fresh
        try:
            from agents.knowledge.email_query_agent import invalidate_email_cache
            invalidate_email_cache()
        except Exception:
            pass
        query = user_input.strip()
        print(f"Assistant: Searching emails for: {query}\n")

        # Get polished presentation-style summary (concise)
        try:
            polished = summarize_emails_by_query(query, max_results=8)
            print("Assistant:", polished, "\n")
        except Exception:
            # Fallback to older raw results if polished summarizer fails
            results = search_emails_by_text(query)
            print("Assistant: (raw results)")
            for r in results:
                print("-", r.get("id"), r.get("subject") or "(no subject)", "from", r.get("from"))
            print()
        continue

    # ======================================
    # DOCUMENT LIST
    # ======================================
    if intent == "DOCUMENT_LIST":
        print("Assistant:", list_all_documents(), "\n")
        continue

    # ======================================
    # RETRIEVAL (RAG)
    # ======================================
    if intent == "RETRIEVAL":
        # If vector DB is still building, fallback to LLM immediately — don't block the user
        if not vector_ready or vector_db is None:
            print("Assistant:", handle_general(user_input, MODEL_NAME), "\n")
            continue

        answer, source = handle_retrieval(
            user_input, vector_db, THRESHOLD, MODEL_NAME
        )
        if answer:
            bullets = _to_bullets(answer, max_bullets=6)
            print("Assistant:")
            for b in bullets:
                print(f"  - {b}")
            if source:
                print(f"  (Source: {source})")
        else:
            # Nothing found in docs; answer from LLM knowledge
            print("Assistant:", handle_general(user_input, MODEL_NAME))
        print()
        continue

    # ======================================
    # SUMMARY
    # ======================================
    if intent == "SUMMARY":
        summary = handle_summary(documents, MODEL_NAME)
        user_l = user_input.lower()
        want_bullets = any(k in user_l for k in ("bullet", "bullets", "bullet points", "in bullets", "give bullets", "list key"))
        if want_bullets or (summary and len(summary) > 800):
            bullets = _to_bullets(summary, max_bullets=8)
            print("Assistant (summary):")
            for b in bullets:
                print(f"- {b}")
            print()
        else:
            print("Assistant:", summary, "\n")
        continue

    # ======================================
    # TOPICS
    # ======================================
    if intent == "TOPIC":
        print("Assistant:", handle_topics(documents, MODEL_NAME), "\n")
        continue

    # ======================================
    # COMPARISONS
    # ======================================
    if intent == "COMPARE":
        resp = handle_compare(user_input)
        print("Assistant:\n")
        print(resp)
        print("\n")
        continue

    # ======================================
    # FALLBACK (LLM)
    # ======================================
    print("Assistant:", handle_general(user_input, MODEL_NAME), "\n")
    