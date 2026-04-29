# MCP Integration — Local AI Assistant

This directory adds a **Model Context Protocol (MCP)** tool layer on top of the
existing agent system.  No existing agent files were rewritten; every tool is a
thin wrapper that delegates to the original agent functions.

> **Why `agent_mcp/` and not `mcp/`?**  
> The installed SDK package is also called `mcp`.  Naming our folder `mcp/`
> would shadow the SDK import inside our own files — Python would resolve
> `from mcp.server.fastmcp import FastMCP` to *our* folder instead of the SDK.
> Renaming to `agent_mcp/` eliminates the collision entirely.

---

## Architecture

```
MCP Client (Claude Desktop / VS Code / custom)
        │   MCP protocol (stdio or SSE)
        ▼
  mcp/server.py             ← FastMCP server — registers all tools
        │   Python function calls
        ▼
  mcp/tools/
    reminders.py            ← wraps agents/tasks/reminder_agent.py
    emails.py               ← wraps agents/knowledge/email_*_agent.py
    documents.py            ← wraps agents/knowledge/retrieval/summary/topic agents
                               + engines/rag_engine.py + ChromaDB
    system.py               ← wraps agents/core/general_agent.py
                                        agents/core/planner_agent.py
        │
        ▼
  agents/**/*_agent.py      ← ORIGINAL agents — completely unchanged
        │
        ▼
  engines/rag_engine.py + ChromaDB + Ollama (llama3)
```

Optional in-process path (no separate server needed):

```
smart_agent.py  →  mcp/bridge.py  →  mcp/tools/*.py  →  agents/**
```

---

## File map

| File | Purpose |
|------|---------|
| `agent_mcp/server.py` | FastMCP server entry point — registers all 13 tools |
| `agent_mcp/bridge.py` | In-process adapter for smart_agent.py; maps intent labels → tool calls |
| `agent_mcp/tools/reminders.py` | `reminders.set / .list / .delete` |
| `agent_mcp/tools/emails.py` | `email.search / .summarize / .list_all` |
| `agent_mcp/tools/documents.py` | `documents.search / .summarize / .topics / .list` |
| `agent_mcp/tools/system.py` | `system.chat / .intent / .status` |

---

## Tool catalogue

### Reminders
| MCP Tool | Description | Key parameters |
|----------|-------------|----------------|
| `reminders.set` | Create a reminder from natural language | `query: str` |
| `reminders.list` | List all pending / fired reminders | — |
| `reminders.delete` | Delete reminders matching a keyword | `keyword: str` |

### Email
| MCP Tool | Description | Key parameters |
|----------|-------------|----------------|
| `email.search` | Semantic + keyword search over inbox | `query: str`, `max_results: int = 8` |
| `email.summarize` | Inbox summary (full or filtered) | `query: str = ""` |
| `email.list_all` | Raw newest-first email list | `limit: int = 20` |

### Documents (RAG)
| MCP Tool | Description | Key parameters |
|----------|-------------|----------------|
| `documents.search` | RAG search — answer from local docs | `query: str`, `model: str` |
| `documents.summarize` | High-level summary of all docs | `model: str` |
| `documents.topics` | Extract main topics across all docs | `model: str` |
| `documents.list` | List document files with size | — |

### System
| MCP Tool | Description | Key parameters |
|----------|-------------|----------------|
| `system.chat` | Free-form LLM conversation | `message: str`, `temperature: float` |
| `system.intent` | Classify user message intent | `message: str` |
| `system.status` | Health check (Ollama, ChromaDB, etc.) | — |

---

## Installation

```bash
# Activate your virtual environment first
cd "C:\Project\Local_AI Agent\local_ai_assistant"
.\venv311\Scripts\activate

# Only one new dependency is needed
.\venv311\Scripts\python.exe -m pip install "mcp[cli]" trio
```

> All other dependencies (ollama, chromadb, langchain, etc.) are already
> installed by the existing project.

---

## Running the MCP server

### Option A — stdio transport (Claude Desktop / VS Code MCP extension)

```bash
# From the project root (local_ai_assistant/)
python -m mcp.server
# or via smart_agent.py:
python smart_agent.py --mcp
```

### Option B — SSE transport (HTTP, multi-client)

```bash
python -m mcp.server --transport sse --port 8765
# or:
python smart_agent.py --mcp-sse
```

### Option C — List registered tools (smoke-test, no server started)

```bash
python -m mcp.server --list-tools
```

---

## Connecting to Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "local-ai-assistant": {
      "command": "C:\\Project\\Local_AI Agent\\local_ai_assistant\\venv311\\Scripts\\python.exe",
      "args": [
        "-m", "agent_mcp.server"
      ],
      "cwd": "C:\\Project\\Local_AI Agent\\local_ai_assistant"
    }
  }
}
```

Restart Claude Desktop.  The 13 tools will appear automatically.

---

## Connecting to VS Code (Copilot / MCP extension)

Add to `.vscode/mcp.json` in the workspace:

```json
{
  "servers": {
    "local-ai-assistant": {
      "type": "stdio",
      "command": "${workspaceFolder}/venv311/Scripts/python.exe",
      "args": ["-m", "agent_mcp.server"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

---

## Using MCPBridge in smart_agent.py (in-process, no separate server)

`smart_agent.py` already has the optional import.  When `mcp` is installed,
`_MCP_BRIDGE` and `_MCP_ENABLED` are available.  Example usage inside the
main loop (add after `intent = decide_intent(user_input)`):

```python
if _MCP_ENABLED:
    mcp_result = _MCP_BRIDGE.dispatch(intent, user_input, vector_db=vector_db)
    if mcp_result is not None:
        print("Assistant:", mcp_result, "\n")
        continue
# ... existing fallback routing continues unchanged ...
```

The bridge returns `None` for any intent it cannot handle, so the existing
routing always acts as a safe fallback.

---

## Adding a new tool

1. Add your implementation function to the relevant `mcp/tools/<category>.py`.
2. Register it in `agent_mcp/server.py` with `@mcp.tool(name="category.action")`.
3. Export it from `mcp/tools/__init__.py`.
4. Add a `dispatch` branch in `mcp/bridge.py` if smart_agent.py should use it.

No existing agent files need to change.

---

## Future integration points

### 1 — Semantic caching

Add a Redis or in-memory TTL cache in front of the expensive tool calls:

```python
# mcp/tools/_cache.py
import hashlib, time
_CACHE: dict = {}

def cached(ttl: int = 60):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            key = hashlib.md5(str((args, kwargs)).encode()).hexdigest()
            if key in _CACHE:
                val, ts = _CACHE[key]
                if time.time() - ts < ttl:
                    return val
            result = fn(*args, **kwargs)
            _CACHE[key] = (result, time.time())
            return result
        return wrapper
    return decorator
```

Apply to high-latency tools:  `@cached(ttl=300)` on `documents_search`,
`email_summarize`, etc.

### 2 — FastAPI background tasks

Mount the MCP server inside `services/api_server.py`:

```python
from fastapi import FastAPI, BackgroundTasks
from mcp.bridge import MCPBridge

app = FastAPI()
bridge = MCPBridge()

@app.post("/ask")
async def ask(query: str, background_tasks: BackgroundTasks):
    intent_result = bridge.dispatch("GENERAL", query, raw=True)
    background_tasks.add_task(log_query, query, intent_result)
    return intent_result
```

### 3 — Knowledge graph integration

Add a new tool `graph.query` in `mcp/tools/graph.py` that wraps a NetworkX
or Neo4j graph built from your documents:

```python
# mcp/tools/graph.py
def graph_query(entity: str) -> dict:
    """Return related entities and relationships for a given entity."""
    from agents.knowledge.graph_agent import query_graph   # future agent
    return query_graph(entity)
```

Register it in `agent_mcp/server.py` as `@mcp.tool(name="graph.query")`.

---

## Backward compatibility checklist

- [x] `smart_agent.py` CLI loop unchanged — `--mcp` / `--mcp-sse` flags are additive
- [x] All existing agents unchanged
- [x] `engines/rag_engine.py` unchanged
- [x] New imports in `smart_agent.py` are wrapped in `try/except` — safe if `mcp` is not installed
- [x] `mcp/tools/documents.py` reuses the same ChromaDB path (`data/vector_store_v2`) as `smart_agent.py`
- [x] No new required dependencies for the CLI path (only `pip install mcp` for MCP mode)
