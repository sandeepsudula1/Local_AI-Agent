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

def get_semantic_mode(user_input: str, has_active_file: bool = False) -> str:
    """LLM-based semantic intent classification with fallback safety."""
    text = user_input.strip()
    if not text:
        return "GENERAL"
        
    if not _HAVE_OLLAMA:
        return "GENERAL"
        
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
            
        # Safety layer: if unsure and active file exists, check context references
        if intent == "UNKNOWN" and has_active_file:
            query_lower = text.lower()
            file_refs = ["it", "this", "above", "document"]
            if any(w in query_lower for w in file_refs):
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

def is_followup_query(user_input: str) -> bool:
    """Determine if the query is a follow-up to the active document using robust regex."""
    text = user_input.strip().lower()
    if not text:
        return False
        
    # Strong signals that explicitly refer to context
    strong_context_signals = [
        r"\babove\b", r"\bthis document\b", r"\bthat document\b", r"\bit\b",
        r"\bthis file\b", r"\bin the document\b", r"\bin the file\b"
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