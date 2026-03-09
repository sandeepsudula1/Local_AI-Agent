"""
mcp/tools/documents.py
======================
MCP tool wrappers for the document / RAG subsystem.

Wraps (without modifying):
  • agents/knowledge/retrieval_agent.py  → handle_retrieval / _detect_target_file
  • agents/knowledge/summary_agent.py    → handle_summary
  • agents/knowledge/topic_agent.py      → handle_topics
  • agents/knowledge/document_list_agent.py → list_all_documents
  • engines/rag_engine.py                → vector DB loading

Exposed MCP tools
-----------------
  documents.search     → semantic RAG search over local documents
  documents.summarize  → summarise all documents
  documents.topics     → extract main topics from documents
  documents.list       → list available document files
"""

from __future__ import annotations

import sys
import os
import threading

# ── project root on path ───────────────────────────────────────────────────
_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT    = os.path.dirname(_MCP_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── existing agents / engines (unchanged) ─────────────────────────────────
from agents.knowledge.retrieval_agent import handle_retrieval
from agents.knowledge.summary_agent   import handle_summary
from agents.knowledge.topic_agent     import handle_topics
from agents.knowledge.document_list_agent import list_all_documents

# ── constants (mirrors smart_agent.py settings) ────────────────────────────
_MODEL_NAME       = "llama3.2:1b"
_THRESHOLD        = 1.5
_EMBEDDING_MODEL  = "sentence-transformers/all-MiniLM-L6-v2"
_DOCS_PATH        = os.path.join(_ROOT, "data", "documents")
_VECTOR_STORE_PATH = os.path.join(_ROOT, "data", "vector_store_v2")

# ── lazy vector DB singleton ───────────────────────────────────────────────
# The MCP server may be started independently of smart_agent.py, so we
# initialise the vector DB here lazily (once, on first use).
_vector_db     = None
_vector_ready  = False
_vector_lock   = threading.Lock()


def _get_vector_db():
    """Return the shared ChromaDB instance, initialising it if needed."""
    global _vector_db, _vector_ready

    if _vector_ready and _vector_db is not None:
        return _vector_db

    with _vector_lock:
        if _vector_ready and _vector_db is not None:
            return _vector_db

        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            from langchain_community.vectorstores import Chroma

            emb = HuggingFaceEmbeddings(
                model_name=_EMBEDDING_MODEL,
                model_kwargs={"device": "cpu"},
            )

            # Force rebuild if any document is newer than the persisted store
            _db_file = os.path.join(_VECTOR_STORE_PATH, "chroma.sqlite3")
            _store_exists = os.path.exists(_VECTOR_STORE_PATH) and any(os.scandir(_VECTOR_STORE_PATH))
            if _store_exists and os.path.exists(_db_file) and os.path.exists(_DOCS_PATH):
                _store_mtime = os.path.getmtime(_db_file)
                for _fn in os.listdir(_DOCS_PATH):
                    _fp = os.path.join(_DOCS_PATH, _fn)
                    if os.path.isfile(_fp) and os.path.getmtime(_fp) > _store_mtime:
                        import shutil as _shutil
                        _shutil.rmtree(_VECTOR_STORE_PATH, ignore_errors=True)
                        _store_exists = False
                        break

            if _store_exists:
                _vector_db = Chroma(
                    persist_directory=_VECTOR_STORE_PATH,
                    embedding_function=emb,
                )
                _vector_ready = True
            else:
                # Build from documents if no persisted store exists
                docs = _load_raw_documents()
                if docs:
                    from langchain_text_splitters import RecursiveCharacterTextSplitter
                    splitter = RecursiveCharacterTextSplitter(
                        chunk_size=800, chunk_overlap=80
                    )
                    chunks = splitter.split_documents(docs)
                    _vector_db = Chroma.from_documents(
                        chunks, emb, persist_directory=_VECTOR_STORE_PATH
                    )
                    _vector_ready = True
        except Exception:
            _vector_db = None
            _vector_ready = False

    return _vector_db


def _load_raw_documents() -> list:
    """
    Load all documents from data/documents/ into LangChain Document objects.
    Mirrors the loading logic in smart_agent.py so behaviour is consistent.
    """
    from langchain_core.documents import Document
    docs = []

    if not os.path.exists(_DOCS_PATH):
        return docs

    for fname in os.listdir(_DOCS_PATH):
        fpath = os.path.join(_DOCS_PATH, fname)
        try:
            if fname.endswith(".pdf"):
                from langchain_community.document_loaders import PyPDFLoader
                for d in PyPDFLoader(fpath).load():
                    d.metadata["source"] = fname
                    docs.append(d)
            elif fname.endswith(".csv"):
                import pandas as pd
                df = pd.read_csv(fpath)
                for _, row in df.iterrows():
                    row_text = ", ".join(f"{c}: {row[c]}" for c in df.columns)
                    docs.append(Document(page_content=row_text, metadata={"source": fname}))
            elif fname.endswith(".txt"):
                text = open(fpath, "r", encoding="utf-8", errors="ignore").read()
                docs.append(Document(page_content=text, metadata={"source": fname}))
            elif fname.lower().endswith((".png", ".jpg", ".jpeg")):
                # Try OCR ingestion; skip silently if Tesseract is not installed.
                # To index images, delete data/vector_store_v2/ and restart so
                # the vector store rebuilds with OCR content.
                try:
                    import pytesseract
                    from PIL import Image as _PILImage
                    pytesseract.pytesseract.tesseract_cmd = (
                        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                    )
                    ocr_text = pytesseract.image_to_string(_PILImage.open(fpath)).strip()
                    if ocr_text:
                        docs.append(Document(page_content=ocr_text, metadata={"source": fname}))
                except Exception:
                    pass  # OCR unavailable — image skipped during indexing
        except Exception:
            pass

    return docs


# ══════════════════════════════════════════════════════════════════════════════
# Tool: documents.search
# ══════════════════════════════════════════════════════════════════════════════
def documents_search(query: str, model: str = _MODEL_NAME) -> dict:
    """
    Answer a question using the local document knowledge base (RAG).

    Searches ChromaDB for relevant chunks, then uses the local LLM
    (Ollama) to synthesise a precise answer grounded in those chunks.

    If the user mentions a specific filename (e.g. "company_data.csv"),
    that file is loaded and queried directly — bypassing vector search.

    Parameters
    ----------
    query : str
        Natural-language question to answer from local documents.
        Examples:
          • "What is the company's annual revenue?"
          • "Summarise the internship report"
          • "What does company_data.csv say about Q3?"
    model : str
        Ollama model name to use (default: llama3.2:1b).

    Returns
    -------
    dict
        {
          "success": bool,
          "query": str,
          "answer": str,
          "source": str    # document filename that provided the answer
        }
    """
    if not query or not query.strip():
        return {
            "success": False,
            "query": query,
            "answer": "No query provided.",
            "source": "",
        }

    vdb = _get_vector_db()  # may be None if no docs indexed yet

    try:
        answer, source = handle_retrieval(
            query.strip(), vdb, _THRESHOLD, model or _MODEL_NAME
        )
        if answer:
            return {
                "success": True,
                "query": query.strip(),
                "answer": answer,
                "source": source or "",
            }
        return {
            "success": False,
            "query": query.strip(),
            "answer": "No relevant content found in local documents.",
            "source": "",
        }
    except Exception as exc:
        return {
            "success": False,
            "query": query.strip(),
            "answer": f"Retrieval failed: {exc}",
            "source": "",
        }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: documents.summarize
# ══════════════════════════════════════════════════════════════════════════════
def documents_summarize(model: str = _MODEL_NAME) -> dict:
    """
    Produce a high-level summary of ALL documents in the knowledge base.

    The summary covers key themes across every document and mentions
    different domains if present (e.g. technical, HR, financial).

    Parameters
    ----------
    model : str
        Ollama model name to use (default: llama3.2:1b).

    Returns
    -------
    dict
        {
          "success": bool,
          "document_count": int,
          "summary": str
        }
    """
    docs = _load_raw_documents()
    if not docs:
        return {
            "success": False,
            "document_count": 0,
            "summary": "No documents found in data/documents/.",
        }

    try:
        summary = handle_summary(docs, model or _MODEL_NAME)
        return {
            "success": True,
            "document_count": len(docs),
            "summary": summary,
        }
    except Exception as exc:
        return {
            "success": False,
            "document_count": len(docs),
            "summary": f"Summarisation failed: {exc}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: documents.topics
# ══════════════════════════════════════════════════════════════════════════════
def documents_topics(model: str = _MODEL_NAME) -> dict:
    """
    Identify and group the main topics covered across all documents.

    Returns bullet-point topics produced by the local LLM.

    Parameters
    ----------
    model : str
        Ollama model name to use (default: llama3.2:1b).

    Returns
    -------
    dict
        {
          "success": bool,
          "document_count": int,
          "topics": str    # bullet-point topic list
        }
    """
    docs = _load_raw_documents()
    if not docs:
        return {
            "success": False,
            "document_count": 0,
            "topics": "No documents found in data/documents/.",
        }

    try:
        topics = handle_topics(docs, model or _MODEL_NAME)
        return {
            "success": True,
            "document_count": len(docs),
            "topics": topics,
        }
    except Exception as exc:
        return {
            "success": False,
            "document_count": len(docs),
            "topics": f"Topic extraction failed: {exc}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# Tool: documents.list
# ══════════════════════════════════════════════════════════════════════════════
def documents_list() -> dict:
    """
    List all document files available in the local knowledge base.

    Returns filenames with their extension and approximate size.

    Returns
    -------
    dict
        {
          "success": bool,
          "count": int,
          "files": list[dict]   # [{"name": str, "ext": str, "size_kb": float}]
          "summary": str        # formatted text list
        }
    """
    try:
        # list_all_documents() returns a formatted string from the agent
        summary = list_all_documents()
    except Exception as exc:
        summary = f"Could not list documents: {exc}"

    _DOC_EXTS = {'.pdf', '.csv', '.txt', '.docx', '.doc', '.xlsx', '.xls', '.png', '.jpg', '.jpeg'}
    files: list[dict] = []
    if os.path.exists(_DOCS_PATH):
        for fname in sorted(os.listdir(_DOCS_PATH)):
            fpath = os.path.join(_DOCS_PATH, fname)
            if not os.path.isfile(fpath):
                continue
            _, ext = os.path.splitext(fname)
            if ext.lower() not in _DOC_EXTS:
                continue
            try:
                size_kb = round(os.path.getsize(fpath) / 1024, 1)
            except Exception:
                size_kb = 0.0
            files.append({"name": fname, "ext": ext.lower(), "size_kb": size_kb})

    return {
        "success": True,
        "count": len(files),
        "files": files,
        "summary": summary,
    }
