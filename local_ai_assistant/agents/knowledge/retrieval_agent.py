from __future__ import annotations

import os
import re
from typing import Optional, Tuple

import pandas as pd

try:
    import ollama
    HAVE_OLLAMA = True
except Exception:
    ollama = None
    HAVE_OLLAMA = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS_PATH = os.path.join(ROOT, "data", "documents")


def _list_doc_files():
    if not os.path.exists(DOCS_PATH):
        return []
    return os.listdir(DOCS_PATH)


def _deduplicate_lines(text: str) -> str:
    """Remove duplicate lines (case-insensitive) from text while preserving order."""
    if not text:
        return text
    seen: set = set()
    out = []
    for line in text.splitlines():
        key = line.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(line)
    return "\n".join(out)


def _relevant_excerpt(text: str, query: str, max_chars: int = 4000) -> str:
    """Return up to max_chars of text starting from the most query-relevant position."""
    if len(text) <= max_chars:
        return text
    _STOP = {
        "what", "when", "where", "which", "that", "this", "have", "from", "with",
        "does", "about", "were", "will", "into", "been", "they", "them", "said",
        "tell", "show", "give", "know", "more", "some", "like", "also", "used",
        "then", "than", "there", "their", "these",
    }
    q_words = {w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 3} - _STOP
    if not q_words:
        return text[:max_chars]
    step = max(100, max_chars // 20)
    best_pos, best_score = 0, 0
    for pos in range(0, max(1, len(text) - max_chars + 1), step):
        chunk = text[pos:pos + max_chars].lower()
        score = sum(chunk.count(w) for w in q_words)
        if score > best_score:
            best_score = score
            best_pos = pos
    return text[best_pos:best_pos + max_chars]


def _detect_target_file(query: str):
    """Return the specific filename the user is asking about, or None."""
    q = query.strip()
    available = _list_doc_files()

    # 1. Direct substring match against known filenames (case-insensitive)
    q_lower = q.lower()
    for fname in available:
        if fname.lower() in q_lower:
            return fname

    # 2. Regex for any word.ext pattern (handles filenames with spaces via greedy match)
    m = re.search(r"[\w\s\-\.,()]+\.(?:pdf|csv|txt|png|jpg|jpeg|docx|xlsx)", q, flags=re.IGNORECASE)
    if m:
        candidate = m.group(0).strip()
        for fname in available:
            if fname.lower() == candidate.lower():
                return fname
        stem = os.path.splitext(candidate)[0].lower()
        for fname in available:
            if stem in fname.lower():
                return fname

    # 3. Keyword match — split filename into words and check if all appear in query
    #    e.g. "sandeep_internship_work.pdf" has keywords [sandeep, internship, work]
    #    query "what does the internship pdf say" contains "internship" → match
    for fname in available:
        ext = os.path.splitext(fname)[1].lower()
        if ext not in {".pdf", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".docx", ".xlsx"}:
            continue
        stem_words = re.findall(r"[a-z]+", os.path.splitext(fname)[0].lower())
        # Match if any meaningful stem word (len>3) appears in the query
        if any(w in q_lower for w in stem_words if len(w) > 3):
            return fname

    return None


def _load_file_content(fname: str):
    """Load text content from a document file. Returns (text, fname)."""
    fpath = os.path.join(DOCS_PATH, fname)
    if not os.path.exists(fpath):
        return None, None
    ext = os.path.splitext(fname)[1].lower()

    if ext == ".pdf":
        try:
            from langchain_community.document_loaders import PyPDFLoader
            docs = list(PyPDFLoader(fpath).load())
            return "\n\n".join(d.page_content for d in docs), fname
        except Exception as e:
            return f"(Error reading PDF: {e})", fname

    elif ext == ".csv":
        try:
            df = pd.read_csv(fpath)
            rows = []
            for i, row in df.iterrows():
                row_parts = [f"{col}={row[col]}" for col in df.columns]
                rows.append(f"Record {i + 1}: {', '.join(row_parts)}")
            table_desc = "\n".join(rows)
            return (
                f"CSV file: {fname}\n"
                f"Columns: {', '.join(df.columns.tolist())}\n"
                f"Number of records: {len(df)}\n"
                f"(Each record is data for the SAME entity, e.g. the same company across different years)\n\n"
                + table_desc
            ), fname
        except Exception as e:
            return f"(Error reading CSV: {e})", fname

    elif ext == ".txt":
        try:
            return open(fpath, "r", encoding="utf-8", errors="ignore").read(), fname
        except Exception as e:
            return f"(Error reading TXT: {e})", fname

    elif ext in {".png", ".jpg", ".jpeg"}:
        try:
            import pytesseract
            from PIL import Image
            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            text = pytesseract.image_to_string(Image.open(fpath)).strip()
            if text:
                return text, fname
            return None, fname   # no readable text — skip LLM, return helpful message below
        except Exception:
            return None, fname   # Tesseract not installed or failed

    elif ext == ".docx":
        try:
            import docx
            doc = docx.Document(fpath)
            return "\n".join(p.text for p in doc.paragraphs), fname
        except Exception as e:
            return f"(Error reading DOCX: {e})", fname

    return "(Unsupported file type)", fname


def _ask_llm(model_name, context, query, source):
    """Ask Ollama to answer using only the provided context. Returns answer string or None."""
    if not HAVE_OLLAMA:
        return None
    try:
        response = ollama.chat(
            model=model_name,
            options={"temperature": 0.0, "num_predict": 200},
            messages=[
                {"role": "system", "content":
                    "You are a concise document analysis assistant. "
                    "Answer using ONLY facts explicitly stated word-for-word in the CONTEXT. "
                    "Be brief — 1 to 3 sentences maximum. "
                    "State numbers, names, and dates directly. "
                    "For CSV/tabular data: each ROW is a separate data record for the SAME entity (e.g. different years for ONE company). "
                    "Do NOT treat different rows or different years as different companies or organizations. "
                    "Read every row and every column carefully and match the correct row to the question. "
                    "Do NOT infer, speculate, or add any information not present in the CONTEXT. "
                    "Do NOT say 'Based on the context' — just state the fact. "
                    "If the context has no relevant information, say exactly: "
                    "'The document does not contain that information.'"},
                {"role": "user", "content":
                    f"Document: {source}\nContext:\n{_relevant_excerpt(context, query)}\n\nQuestion: {query}\n\nAnswer:"},
            ],
        )
        answer = response.get("message", {}).get("content", "").strip()
        if answer:
            return answer
    except Exception:
        pass
    return None


def handle_retrieval(
    query: str,
    vector_db,
    threshold: float,
    model_name: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Answer a document query.

    Rule 1: If the user mentions a specific filename (any extension) →
            load ONLY that file and answer from it exclusively.

    Rule 2: Otherwise → use vector search across ALL indexed sources.
            Every source document contributes its best-matching chunk
            so that smaller files (e.g. a 2-row CSV) are not silenced
            by a large PDF with many more chunks in the top-k results.
    """

    # ── Rule 1: specific file mentioned ──────────────────────────────────────
    target_file = _detect_target_file(query)
    if target_file:
        ext = os.path.splitext(target_file)[1].lower()
        content, source = _load_file_content(target_file)

        # Image files: return OCR result directly — never through Q&A LLM
        if ext in (".png", ".jpg", ".jpeg"):
            if not content:
                return (
                    f"'{target_file}' is an image file. "
                    "OCR (Tesseract) could not extract readable text from it. "
                    "Install Tesseract (https://github.com/tesseract-ocr/tesseract) to enable image text extraction.",
                    target_file,
                )
            return f"OCR-extracted text from '{target_file}':\n{content}", target_file

        if content is None:
            return f"Could not read '{target_file}'.", target_file
        answer = _ask_llm(model_name, content, query, source)
        if answer:
            return answer, source
        return content[:2000], source

    # ── Rule 2: vector search, multi-source aware ─────────────────────────────
    if vector_db is None:
        return None, None

    try:
        results = vector_db.similarity_search_with_score(query, k=10)
    except Exception:
        return None, None

    if not results:
        return None, None

    # Include the best-scoring chunk from EVERY source document so that
    # smaller files (e.g. a 2-row CSV) are not crowded out by a large PDF
    # that dominates the top-k slots.
    best_per_source: dict = {}
    for doc, score in results:
        src = doc.metadata.get("source", "")
        if src not in best_per_source or score < best_per_source[src][1]:
            best_per_source[src] = (doc, score)

    # Sort sources by best score (lowest = most relevant first)
    sorted_sources = sorted(best_per_source.items(), key=lambda x: x[1][1])
    best_source = sorted_sources[0][0]

    # Only attribute sources whose best chunk is within relevance threshold
    relevant_sources = [(src, pair) for src, pair in sorted_sources if pair[1] <= threshold]
    if not relevant_sources:
        relevant_sources = sorted_sources[:1]  # always include at least the best match

    # One representative chunk per relevant source, then 2 extra from top source for depth
    ordered_docs = [pair[0] for _, pair in relevant_sources]
    seen_ids = {id(d) for d in ordered_docs}
    extras = [
        doc for doc, _ in results
        if doc.metadata.get("source", "") == best_source and id(doc) not in seen_ids
    ][:2]
    all_docs = ordered_docs + extras

    context = "\n\n".join(doc.page_content for doc in all_docs)
    context = _deduplicate_lines(context)
    source = ", ".join(src for src, _ in relevant_sources)

    # For any CSV source in the results, replace its chunk with the full table so
    # the LLM sees ALL rows and cannot confuse different years as different companies.
    csv_supplements = []
    for src, _ in relevant_sources:
        if src.lower().endswith(".csv"):
            full_text, _ = _load_file_content(src)
            if full_text and not full_text.startswith("(Error"):
                csv_supplements.append(f"[Full table: {src}]\n{full_text}")
    if csv_supplements:
        context = "\n\n".join(csv_supplements) + "\n\n" + context

    answer = _ask_llm(model_name, context, query, source)
    if answer:
        return answer, source

    snippet = context if len(context) < 2000 else context[:2000] + "..."
    return snippet, source