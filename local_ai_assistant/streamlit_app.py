"""
streamlit_app.py
================
Streamlit Web UI for the Local AI Assistant.

Mirrors the same boot sequence as main.py (services, memory, orchestrator),
but presents a modern browser-based chat interface instead of a CLI.

Run
---
  venv311\\Scripts\\streamlit run streamlit_app.py

Features
--------
* Chat bubbles with full conversation history
* Intent label, tool used, source, and latency shown under each reply
* Sidebar: system status, memory facts, clear memory, tool catalogue
* Special commands: "list tools", "forget everything", "what do you remember"
* Background services started once via @st.cache_resource
"""

from __future__ import annotations

import os
import sys
import json
import time

# ── 0. Path & environment setup (mirrors main.py) ────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Local AI Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Intent → display tool name lookup ────────────────────────────────────────
_INTENT_TOOL_DISPLAY: dict[str, str] = {
    "RETRIEVAL":         "documents.search",
    "SUMMARY":           "documents.summarize",
    "TOPIC":             "documents.topics",
    "DOCUMENT_LIST":     "documents.list",
    "EMAIL_SEARCH":      "email.search",
    "EMAIL_SUMMARY":     "email.summarize",
    "REMINDER_SET":      "reminders.set",
    "REMINDER_LIST":     "reminders.list",
    "REMINDER_DELETE":   "reminders.delete",
    "AUDIO_TRANSCRIBE":  "audio.transcribe",
    "AUDIO_QUERY":       "audio.query",
    "AUDIO_LIST":        "audio.list",
    "COMPARE":           "documents.search",
    "CHAT":              "LLM",
    "GENERAL":           "LLM",
    "GREETING":          "LLM",
    "TIME":              "system",
    "DATE":              "system",
    "HELP":              "LLM",
    "UNKNOWN":           "LLM",
}

# ── Service boot (cached — runs exactly once per Streamlit process) ───────────
@st.cache_resource(show_spinner="🚀 Booting AI assistant…")
def _boot() -> tuple:
    """Start all background services and return singletons."""
    from core.logging_config import setup_logging
    from configs.settings import settings

    setup_logging(level=settings.log_level, log_format=settings.log_format)

    from services.document_service import document_service
    from services.vector_store_service import vector_store_service
    from services.reminder_service import reminder_service

    documents = document_service.load_all()
    vector_store_service.start(documents=documents)
    reminder_service.start()

    # Background email polling daemon
    import threading

    def _email_poll() -> None:
        from agents.knowledge.email_query_agent import invalidate_email_cache
        try:
            from agents.tasks.email_agent import EmailAgent
        except ImportError:
            return
        from configs.settings import settings as _s
        while True:
            time.sleep(60)
            try:
                invalidate_email_cache()
                agent = EmailAgent()
                emails = (
                    agent.fetch_recent_emails(last_n=_s.email_fetch_count)
                    if hasattr(agent, "fetch_recent_emails")
                    else agent.fetch_unread_emails()
                )
                if emails:
                    agent.save_to_cache(emails)
            except Exception:
                pass

    threading.Thread(target=_email_poll, daemon=True, name="email-poller").start()

    # Initial email sync (best-effort)
    try:
        from agents.tasks.email_agent import EmailAgent as _EA
        from agents.knowledge.email_query_agent import invalidate_email_cache as _inv
        _inv()
        _ea = _EA()
        _new = (
            _ea.fetch_recent_emails(last_n=settings.email_fetch_count)
            if hasattr(_ea, "fetch_recent_emails")
            else _ea.fetch_unread_emails()
        )
        if _new:
            _ea.save_to_cache(_new)
    except Exception:
        pass

    from pipelines.orchestrator import orchestrator
    orchestrator.startup()
    return orchestrator, document_service, vector_store_service, settings


orchestrator, _doc_svc, _vs_svc, _settings = _boot()


# ── Helper functions ──────────────────────────────────────────────────────────

def _get_memory():
    try:
        from memory.conversation_memory import conversation_memory
        return conversation_memory
    except Exception:
        return None


def _get_tool_catalog():
    try:
        from tools.tool_registry import tool_catalog
        return tool_catalog
    except Exception:
        return None


def _system_status() -> dict:
    """Return a status dict for display in the sidebar."""
    result: dict[str, tuple[str, str]] = {}

    # Ollama
    try:
        import ollama
        ollama.list()
        result["Ollama"] = ("✅ Online", f"Model: `{_settings.model_name}`")
    except Exception as exc:
        result["Ollama"] = ("❌ Offline", str(exc)[:60])

    # Vector store
    try:
        db = _vs_svc.get_vector_db()
        if db is not None:
            result["Vector Store"] = ("✅ Ready", "ChromaDB loaded")
        else:
            result["Vector Store"] = ("⏳ Building…", "Loading in background")
    except Exception:
        result["Vector Store"] = ("❌ Error", "")

    # Documents
    try:
        docs = _doc_svc.get_documents()
        count = len(docs) if docs else 0
        result["Documents"] = ("✅ Loaded", f"{count:,} chunk(s)")
    except Exception:
        result["Documents"] = ("❓ Unknown", "")

    # Reminders
    try:
        try:
            from configs.settings import DATA_DIR as _ST_DATA_DIR
            rpath = os.path.join(str(_ST_DATA_DIR), "reminders.json")
        except Exception:
            rpath = os.path.join(_ROOT, "data", "reminders.json")
        with open(rpath, encoding="utf-8") as f:
            reminders = json.load(f)
        active = [r for r in reminders if not r.get("fired", False)]
        result["Reminders"] = ("📅 Active", f"{len(active)} pending")
    except FileNotFoundError:
        result["Reminders"] = ("—", "No reminders yet")
    except Exception:
        result["Reminders"] = ("⚠️ Error", "")

    return result


def _render_meta(meta: dict) -> None:
    """Render intent / tool / latency / source as a compact row."""
    intent = meta.get("intent", "—")
    tool = meta.get("tool", "LLM")
    latency = meta.get("latency_ms", 0)
    source = meta.get("source") or ""

    cols = st.columns([2, 2, 1, 3])
    cols[0].caption(f"🎯 **{intent}**")
    cols[1].caption(f"🔧 `{tool}`")
    cols[2].caption(f"⏱️ {latency:.0f} ms")
    if source:
        cols[3].caption(f"📄 {source[:60]}")


# ── Session state defaults ────────────────────────────────────────────────────
if "messages" not in st.session_state:
    # Each entry: {"role": "user"|"assistant", "content": str, "meta": dict|None}
    st.session_state.messages = []

if "first_run" not in st.session_state:
    st.session_state.first_run = True

if "pending_permission_folder" not in st.session_state:
    st.session_state.pending_permission_folder = None


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🤖 Local AI Assistant")
    st.caption("Offline • Multi-Agent • Local LLM")
    st.divider()

    # ── System status ─────────────────────────────────────────────────────────
    with st.expander("⚡ System Status", expanded=True):
        for svc_name, (icon, detail) in _system_status().items():
            st.markdown(f"**{svc_name}** {icon}")
            if detail:
                st.caption(detail)
        if st.button("🔄 Refresh Status", use_container_width=True, key="refresh_status"):
            st.cache_resource.clear()
            st.rerun()

    st.divider()

    # ── Memory facts ──────────────────────────────────────────────────────────
    with st.expander("🧠 What I Remember", expanded=False):
        mem = _get_memory()
        if mem:
            facts = mem.list_facts()
            if facts:
                for k, v in facts.items():
                    st.markdown(f"**{k.title()}**: {v}")
            else:
                st.caption("No facts stored yet. Tell me your name or preferences!")
        else:
            st.caption("Memory unavailable.")

    # ── Danger zone ───────────────────────────────────────────────────────────
    with st.expander("⚠️ Actions", expanded=False):
        if st.button("🗑️ Forget Everything", use_container_width=True, key="forget_btn"):
            mem = _get_memory()
            if mem:
                mem.clear()
            st.session_state.messages = []
            st.success("Memory cleared!")
            st.rerun()
        if st.button("🧹 Clear Chat History", use_container_width=True, key="clear_chat"):
            st.session_state.messages = []
            st.rerun()

    # ── Folder Permissions ────────────────────────────────────────────────────
    with st.expander("🔐 Folder Permissions", expanded=False):
        try:
            from core.access_control import ALLOWED_FOLDERS as _AF
            from core.permission_store import permission_store as _ps
            st.caption("**Always allowed (static):**")
            for _f in _AF:
                st.markdown(f"- `{_f}`")
            _dyn = _ps.get_granted_folders()
            if _dyn:
                st.caption("**Dynamically granted:**")
                for _f in sorted(_dyn):
                    cols_p = st.columns([4, 1])
                    cols_p[0].markdown(f"- `{_f}`")
                    if cols_p[1].button("✖", key=f"revoke_{_f}", help=f"Revoke {_f}"):
                        _ps.revoke(_f)
                        st.success(f"Revoked: {_f}")
                        st.rerun()
            else:
                st.caption("No dynamically-granted folders yet.")
        except Exception:
            st.caption("Permission store unavailable.")

    st.divider()

    # ── Tool catalogue ────────────────────────────────────────────────────────
    with st.expander("🛠️ Available Tools", expanded=False):
        tc = _get_tool_catalog()
        if tc:
            st.code(tc.describe_all(), language=None)
        else:
            st.caption("Tool catalog unavailable.")

    st.divider()
    st.caption(f"Model: `{_settings.model_name}`")
    st.caption(f"Embed: `{_settings.embedding_model}`")
    st.caption("Powered by Ollama · ChromaDB · Whisper")


# ── Main content area ─────────────────────────────────────────────────────────
st.title("💬 Local AI Assistant")
st.caption(
    "Ask about your **documents**, **emails**, **reminders**, or anything else. "
    "All processing is local — nothing leaves your machine."
)

# Show sample queries on first visit
if st.session_state.first_run and not st.session_state.messages:
    st.session_state.first_run = False
    with st.container():
        st.info(
            "**Try asking:**\n"
            "- How many employees in 2024?\n"
            "- Remind me at 15:30 to call Alice\n"
            "- Show me emails from marketing\n"
            "- Compare Python vs JavaScript\n"
            "- What tools are available?"
        )

# ── Replay stored messages ────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("meta"):
            _render_meta(msg["meta"])

# ── Chat input ────────────────────────────────────────────────────────────────
if prompt := st.chat_input("Ask me anything…"):
    lower = prompt.strip().lower()

    # ── Display user message ──────────────────────────────────────────────────
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt, "meta": None})

    # ── Special commands ──────────────────────────────────────────────────────
    if lower in ("list tools", "tools", "what tools do you have"):
        tc = _get_tool_catalog()
        answer = f"```\n{tc.describe_all()}\n```" if tc else "Tool catalog unavailable."
        meta = {"intent": "TOOL_LIST", "tool": "catalog", "latency_ms": 0}
        with st.chat_message("assistant"):
            st.markdown(answer)
            _render_meta(meta)
        st.session_state.messages.append({"role": "assistant", "content": answer, "meta": meta})

    elif lower in ("forget everything", "clear memory", "reset memory"):
        mem = _get_memory()
        if mem:
            mem.clear()
        st.session_state.messages = []
        with st.chat_message("assistant"):
            st.markdown("✅ Memory and chat history cleared. Starting fresh!")
        st.rerun()

    elif lower in ("what do you remember", "what do you know about me", "memory", "show memory"):
        mem = _get_memory()
        facts = mem.list_facts() if mem else {}
        if facts:
            lines = "\n".join(f"- **{k.title()}**: {v}" for k, v in facts.items())
            answer = f"Here's what I remember about you:\n\n{lines}"
        else:
            answer = "I don't have any stored facts about you yet. Tell me your name or share some info!"
        meta = {"intent": "MEMORY_LIST", "tool": "memory", "latency_ms": 0}
        with st.chat_message("assistant"):
            st.markdown(answer)
            _render_meta(meta)
        st.session_state.messages.append({"role": "assistant", "content": answer, "meta": meta})

    # ── Normal orchestrator dispatch ──────────────────────────────────────────
    else:
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                t0 = time.perf_counter()
                try:
                    response = orchestrator.run(prompt)
                    answer = response.answer.strip() if response.answer else "_(no response)_"
                    tool_display = _INTENT_TOOL_DISPLAY.get(response.intent, "LLM")
                    meta = {
                        "intent": response.intent,
                        "tool": tool_display,
                        "latency_ms": response.latency_ms,
                        "source": response.source,
                    }
                except Exception as exc:
                    answer = f"⚠️ **Error**: {exc}"
                    meta = {
                        "intent": "ERROR",
                        "tool": "—",
                        "latency_ms": (time.perf_counter() - t0) * 1_000,
                        "source": None,
                    }

            st.markdown(answer)
            _render_meta(meta)

            # ── Permission request: show inline Grant / Deny buttons ──────────
            if response.intent == "REQUEST_PERMISSION":
                try:
                    from core.permission_store import permission_store as _ps
                    _pf, _pq = _ps.get_pending()
                    if _pf:
                        _g_col, _d_col = st.columns(2)
                        if _g_col.button("✅ Grant Access", key=f"grant_{_pf}", use_container_width=True):
                            _ps.grant(_pf)
                            _ps.clear_pending()
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": answer,
                                "meta": meta,
                            })
                            _rerun_resp = orchestrator.run(_pq)
                            _rerun_ans = (
                                f"✅ Access granted to `{_pf}`.\n\n"
                                + (_rerun_resp.answer or "")
                            )
                            _rerun_meta = {
                                "intent": "PERMISSION_GRANTED",
                                "tool": "permission",
                                "latency_ms": _rerun_resp.latency_ms,
                                "source": _rerun_resp.source,
                            }
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": _rerun_ans,
                                "meta": _rerun_meta,
                            })
                            st.rerun()
                        if _d_col.button("❌ Deny Access", key=f"deny_{_pf}", use_container_width=True):
                            _ps.clear_pending()
                            _deny_ans = "Access request denied. I will not use that folder."
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": answer,
                                "meta": meta,
                            })
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": _deny_ans,
                                "meta": {"intent": "PERMISSION_DENIED", "tool": "permission", "latency_ms": 0, "source": None},
                            })
                            st.rerun()
                except Exception:
                    pass

        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "meta": meta,
        })
