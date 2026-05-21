# agents/core/planner_agent.py
#
# Intent classification using the local LLM (ollama) so the assistant
# understands natural language instead of only rigid keyword commands.
# Regex fast-paths are kept for the most obvious unambiguous patterns
# (greetings, current time/date) to avoid unnecessary LLM calls.

import re

try:
    import ollama as _ollama
    _HAVE_OLLAMA = True
except Exception:
    _ollama = None
    _HAVE_OLLAMA = False

from configs.llm_config import MODEL
_MODEL = MODEL

def get_semantic_mode(user_input: str, has_active_file: bool = False, active_file_name: str = "") -> str:
    """LLM-based semantic intent classification with conversational context awareness."""
    text = user_input.strip()
    if not text:
        return "GENERAL"
        
    if not _HAVE_OLLAMA:
        return "GENERAL"

    # ── Build context-aware classification prompt ─────────────────────────────
    # When an active file is open, the LLM must understand that questions
    # referring to document content, structure, or data are FILE_QA — not FILE_SEARCH.
    # We inject document context and richer disambiguation examples when needed.

    if has_active_file:
        active_ctx = f" (currently open: {active_file_name})" if active_file_name else ""
        prompt = f"""You are an intent classifier for an AI assistant. The user has an active document open{active_ctx}.

Classify the user's query into exactly ONE of these labels:
EMAIL_SEARCH, EMAIL_REPLY, EMAIL_COMPOSE, FILE_SEARCH, FILE_QA, GENERAL

Label definitions:
* EMAIL_SEARCH  - user wants to find or read emails
* EMAIL_REPLY   - user wants to reply to an existing email
* EMAIL_COMPOSE - user wants to write or send a new email
* FILE_SEARCH   - user wants to find, discover, or open a DIFFERENT or NEW document
* FILE_QA       - user is asking a question about the CURRENTLY OPEN document (may use words like: it, this, above, document, mentioned, here, the file, in this, from this)
* GENERAL       - general knowledge question unrelated to any document or email

CRITICAL RULE 1: If the user asks about content, data, or information INSIDE the currently open document — classify as FILE_QA.

CRITICAL RULE 2: If the user is asking whether OTHER documents exist, or searching across the document collection, or asking you to find/discover related files — classify as FILE_SEARCH, even if the query contains words like 'document' or 'file'.

KEY DISTINCTION:
- FILE_QA  = asking about content of the OPEN document  ("what does it say about X")
- FILE_SEARCH = asking whether OTHER documents exist or requesting discovery ("do you see any document about X", "is there a file related to Y", "any other document containing Z")

Examples with active document open:
Input: "summarize it"
-> FILE_QA

Input: "what is education in above document"
-> FILE_QA

Input: "how many credits mentioned in the above document"
-> FILE_QA

Input: "how many semesters"
-> FILE_QA

Input: "what grades did the student get"
-> FILE_QA

Input: "how many subjects mentioned"
-> FILE_QA

Input: "what is the cgpa"
-> FILE_QA

Input: "tell me the total credits"
-> FILE_QA

Input: "list all subjects from the document"
-> FILE_QA

Input: "what approval flow is mentioned"
-> FILE_QA

Input: "what modules are present"
-> FILE_QA

Input: "do you see any document related to VPA and PACS"
-> FILE_SEARCH

Input: "is there any file about port access control"
-> FILE_SEARCH

Input: "any document containing requirements for PACS"
-> FILE_SEARCH

Input: "find document related to vizag port"
-> FILE_SEARCH

Input: "open another pdf"
-> FILE_SEARCH

Input: "search files about machine learning"
-> FILE_SEARCH

Input: "show me a different document"
-> FILE_SEARCH

Input: "find me the resume of john"
-> FILE_SEARCH

Input: "are there any other files related to this topic"
-> FILE_SEARCH

Input: "find email from akshitha"
-> EMAIL_SEARCH

Input: "reply to above email"
-> EMAIL_REPLY

Input: "send a mail to john"
-> EMAIL_COMPOSE

Input: "what is artificial intelligence"
-> GENERAL

Input: "how do I sort a list in python"
-> GENERAL

Input: "{text}"
->"""
    else:
        # No active file — use the standard prompt
        prompt = f"""Classify the user intent into ONE of:
EMAIL_SEARCH, EMAIL_REPLY, EMAIL_COMPOSE, FILE_SEARCH, FILE_QA, GENERAL

Rules:
* EMAIL_SEARCH: user wants to find emails
* EMAIL_REPLY: user wants to reply to an existing email
* EMAIL_COMPOSE: user wants to write/send a new email
* FILE_SEARCH: User wants to find, locate, or discover a document (even if word 'find' is not used)
* FILE_QA: User is referring to an already selected file (uses words like: it, this, above, document)
* GENERAL: Everything else

Return ONLY the label.

Examples:
Input: "find email from akshitha"
-> EMAIL_SEARCH
Input: "do you see any emails about timesheet"
-> EMAIL_SEARCH
Input: "reply to above"
-> EMAIL_REPLY
Input: "write back saying I will update"
-> EMAIL_REPLY
Input: "tell her I'll update"
-> EMAIL_REPLY
Input: "draft a response"
-> EMAIL_REPLY
Input: "send a mail to akshitha"
-> EMAIL_COMPOSE
Input: "find resume"
-> FILE_SEARCH
Input: "show me documents about pizza"
-> FILE_SEARCH
Input: "summarize it"
-> FILE_QA
Input: "what is education in above document"
-> FILE_QA
Input: "what is AI"
-> GENERAL
Input: "how to find max value in array"
-> GENERAL

Input: "{text}"
->"""

    try:
        response = _ollama.generate(model=_MODEL, prompt=prompt, options={"temperature": 0.0})
        label = response.get("response", "").strip().upper()
        
        if "EMAIL_SEARCH" in label:
            intent = "EMAIL_SEARCH"
        elif "EMAIL_REPLY" in label:
            intent = "EMAIL_REPLY"
        elif "EMAIL_COMPOSE" in label:
            intent = "EMAIL_COMPOSE"
        elif "FILE_SEARCH" in label:
            intent = "FILE_SEARCH"
        elif "FILE_QA" in label:
            intent = "FILE_QA"
        elif "GENERAL" in label:
            intent = "GENERAL"
        else:
            intent = "UNKNOWN"
            
        # Safety layer: if unsure and active file exists, lean toward FILE_QA
        if intent == "UNKNOWN" and has_active_file:
            query_lower = text.lower()
            file_refs = ["it", "this", "above", "document", "file", "mentioned", "here", "memo"]
            if any(w in query_lower.split() for w in file_refs):
                intent = "FILE_QA"
            else:
                intent = "GENERAL"
        elif intent == "UNKNOWN":
            intent = "GENERAL"
            
        print(f"[ROUTER] Semantic intent classified as: {intent}")
        return intent
        
    except Exception as e:
        print(f"[ROUTER Error] LLM classification failed: {e}")
        return "GENERAL"


def check_context_link(query: str, active_file_name: str, history: list) -> dict:
    """
    Semantically evaluate whether the query is:
      (A) A CONTINUATION — asking about content inside the active document
      (B) DOCUMENT DISCOVERY — asking whether OTHER documents exist / searching the collection
      (C) A NEW TASK — email, general knowledge, or unrelated activity

    (B) and (C) both return is_linked=False so the router switches to FILE_SEARCH/GENERAL.

    Returns a dict with:
        - is_linked (bool): True only if the query is about the active document's content
        - confidence (str): "HIGH" | "MEDIUM" | "LOW"
        - reasoning (str): Short explanation from the LLM
        - semantic_scope (str): "ACTIVE_DOC" | "DOCUMENT_COLLECTION" | "OTHER"
    """
    if not _HAVE_OLLAMA:
        return {"is_linked": False, "confidence": "LOW",
                "reasoning": "Ollama unavailable", "semantic_scope": "OTHER"}

    # Build a recent history string (last 3 turns max)
    history_text = ""
    for turn in history[-3:]:
        role = turn.get("role", "user").capitalize()
        content = turn.get("content", "")[:200]
        history_text += f"{role}: {content}\n"

    prompt = f"""You are a semantic scope analyzer for an AI document assistant.

The user currently has this document open: "{active_file_name}"

Recent conversation:
{history_text if history_text else "(no prior conversation)"}

New user query: "{query}"

Your task: Identify the TARGET of the user's query.

There are THREE possible targets:

1. ACTIVE_DOC   - The user is asking about content, data, structure, or details INSIDE the
                  currently open document. The answer should come from that document.
                  Examples: "summarize it", "what modules are mentioned", "how many credits",
                  "what approval flow is described", "what is the cgpa", "list the subjects"

2. COLLECTION   - The user is asking whether OTHER documents exist, or wants to search/discover
                  files across the knowledge base. The user is NOT asking about the open document's
                  content — they want to find DIFFERENT or ADDITIONAL documents.
                  Examples: "do you see any document related to VPA",
                            "is there any file about port access control",
                            "any other document containing PACS requirements",
                            "find documents about machine learning",
                            "are there related files on this topic"

3. OTHER        - The user is asking about email, general knowledge, or something completely
                  unrelated to documents.
                  Examples: "find email from john", "what is python", "open another pdf"

KEY INSIGHT: If the query asks "do you see", "is there", "any document", "any file",
"related to", "containing", "about" in the context of finding/discovering documents —
that is always COLLECTION, never ACTIVE_DOC.

Reasoning examples:
Query: "what approval flow is mentioned"         -> ACTIVE_DOC   (asking content of open doc)
Query: "summarize it"                            -> ACTIVE_DOC   (referring to open doc)
Query: "how many semesters"                      -> ACTIVE_DOC   (data from open doc)
Query: "do you see any document related to VPA" -> COLLECTION   (searching for other docs)
Query: "is there a file about port security"     -> COLLECTION   (discovering other docs)
Query: "any document containing PACS info"       -> COLLECTION   (searching the collection)
Query: "find another document about this topic"  -> COLLECTION   (wants a different doc)
Query: "find email from akshitha"                -> OTHER        (email, not document)
Query: "what is machine learning"                -> OTHER        (general knowledge)

Answer with EXACTLY one word: ACTIVE_DOC, COLLECTION, or OTHER
->"""

    try:
        response = _ollama.generate(model=_MODEL, prompt=prompt, options={"temperature": 0.0})
        label = response.get("response", "").strip().upper()

        # Parse the three-way classification
        if "ACTIVE_DOC" in label:
            scope = "ACTIVE_DOC"
            is_linked = True
            confidence = "HIGH"
            reasoning = "LLM confirmed query asks about content of the active document"
        elif "COLLECTION" in label:
            scope = "DOCUMENT_COLLECTION"
            is_linked = False
            confidence = "HIGH"
            reasoning = "LLM identified query as document-collection discovery (searching for other docs)"
        elif "OTHER" in label:
            scope = "OTHER"
            is_linked = False
            confidence = "HIGH"
            reasoning = "LLM identified query as unrelated to the active document"
        else:
            # Ambiguous — be conservative about document collection queries:
            # if the word 'document' appears but query doesn't look like a content question,
            # default to NOT_LINKED so we don't trap the user in the active doc.
            query_lower = query.lower()
            doc_discovery_signals = [
                r"\bany\s+(?:document|file|pdf)\b",
                r"\bdo\s+you\s+see\b",
                r"\brelated\s+to\b",
                r"\bsearch\s+(?:for\s+)?(?:document|file)\b",
                r"\bfind\s+(?:another|other|different|a)\b",
                r"\bother\s+(?:document|file)\b",
                r"\bany\s+other\b"
            ]
            if any(re.search(p, query_lower) for p in doc_discovery_signals):
                scope = "DOCUMENT_COLLECTION"
                is_linked = False
                confidence = "MEDIUM"
                reasoning = f"Ambiguous LLM ({label!r}); discovery signals detected, defaulting to NOT_LINKED"
            else:
                scope = "ACTIVE_DOC"
                is_linked = True
                confidence = "LOW"
                reasoning = f"Ambiguous LLM ({label!r}); no discovery signals, defaulting to LINKED"

        return {
            "is_linked": is_linked,
            "confidence": confidence,
            "reasoning": reasoning,
            "semantic_scope": scope
        }

    except Exception as e:
        print(f"[CONTEXT_LINK Error] {e}")
        return {"is_linked": True, "confidence": "LOW",
                "reasoning": "LLM error; defaulting to LINKED (safe fallback)",
                "semantic_scope": "ACTIVE_DOC"}


def is_followup_query(user_input: str) -> bool:
    """Determine if the query is a follow-up to the active document using robust regex."""
    text = user_input.strip().lower()
    if not text:
        return False
        
    # Strong signals that explicitly refer to context
    strong_context_signals = [
        r"\babove\b", r"\bthis document\b", r"\bthat document\b", r"\bit\b",
        r"\bthis file\b", r"\bin the document\b", r"\bin the file\b",
        r"\bmentioned\b", r"\bin this memo\b", r"\bfrom the document\b",
        r"\bfrom this\b", r"\bfrom above\b"
    ]
    if any(re.search(s, text) for s in strong_context_signals):
        return True

    # Intent-based signals
    followup_signals = [
        r'\b(summ?[ae]ri[sz]e?|summary|gist|tl;?dr)\b',
        r'\b(expl[ai]{1,2}n|details|overview|outline)\b',
        r'\b(read|show|get|fetch|content|inside)\b',
        r'\b(it|this|that|above|file|doc|document|report|resume)\b'
    ]
    
    word_count = len(text.split())
    has_signal = any(re.search(s, text) for s in followup_signals)
    
    if has_signal and word_count <= 8:
        return True
        
    # Anaphoric references with an action verb
    if re.search(r'\b(it|this|that)\b', text) and any(kw in text for kw in ["tell", "show", "give", "is", "was", "read"]):
        return True

    return False


def _llm_classify(user_input: str) -> str:
    """Classify the user intent using the local LLM."""
    if not _HAVE_OLLAMA:
        return "GENERAL"
    
    # We keep this for backward compatibility but it should be avoided if possible
    return "GENERAL"