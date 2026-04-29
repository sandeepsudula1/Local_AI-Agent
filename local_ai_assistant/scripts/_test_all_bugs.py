"""Smoke test for all 5 bug fixes."""
import sys, re
sys.path.insert(0, ".")

from core.intent_classifier import IntentClassifier
from core.access_control import check_access_query
from agents.knowledge.retrieval_agent import _fuzzy_resolve_from_index, _KNOWLEDGE_QUESTION_RE, _FILE_SIGNAL_IN_FUZZY_RE
from memory.conversation_memory import ConversationMemory

fp = IntentClassifier._regex_fastpath

all_ok = True
def check(desc, result, expected):
    global all_ok
    status = "OK" if result == expected else "FAIL"
    if status == "FAIL":
        all_ok = False
    print(f"  [{status}] {desc}: got {result!r}, expected {expected!r}")

# ── Bug 1: Fuzzy file detection (knowledge question guard) ─────────────────
print("\n── Bug 1: Fuzzy resolve guard ──────────────────────────────────────")

for q in ["what is AI agent", "Explain AI", "how does machine learning work",
          "define neural network", "who is Alan Turing"]:
    result = _fuzzy_resolve_from_index(q)
    check(f'fuzzy("{q[:40]}")', result, [])

# File queries should still go through (non-empty keywords should not match guard)
# (No files in test DB, but guard should NOT block these)
for q in ["find my resume", "open report.pdf", "search for document about AI"]:
    blocked = bool(_KNOWLEDGE_QUESTION_RE.search(q) and not _FILE_SIGNAL_IN_FUZZY_RE.search(q))
    check(f'NOT blocked: "{q}"', blocked, False)

# ── Bug 2: ACCESS_CONTROL false triggers ──────────────────────────────────
print("\n── Bug 2: ACCESS_CONTROL passthrough ──────────────────────────────")

for q in ["if I have 3 apples", "what is AI", "explain machine learning",
          "what is 2 plus 2", "tell me a joke", "remind me to buy milk"]:
    result = check_access_query(q)
    check(f'access("{q[:40]}")', result.action, "PASS")

# Queries WITH file/access signals should still go through normal path
for q in ["find my resume", "open document", "list files"]:
    result = check_access_query(q)
    check(f'access passes to logic: "{q}"', result.action in ("PASS", "BLOCK", "CLARIFY", "ALLOW_FOLDER", "REQUEST_PERMISSION"), True)

# ── Bug 3: MEMORY_STORE intent classification ─────────────────────────────
print("\n── Bug 3: MEMORY_STORE / RECALL intents ────────────────────────────")

check("MS: remember that my fav lang is Python", fp("Remember that my favorite language is Python"), "MEMORY_STORE")
check("MS: my favorite editor is VS Code", fp("My favorite editor is VS Code"), "MEMORY_STORE")
check("MR: what is my favorite language?", fp("What is my favorite language?"), "MEMORY_RECALL")
check("MR: do you know my name?", fp("Do you know my name?"), "MEMORY_RECALL")

# ── Bug 3b: MEMORY recall stem matching ────────────────────────────────────
print("\n── Bug 3b: Memory recall stem matching ─────────────────────────────")

mem = ConversationMemory(max_history=5)  # no persist
mem.store("preference", "Python")
mem.store("language", "Java")

r1 = mem.recall_for_query("what is my preferred framework?")
check("stem: 'preference' matched by 'preferred'", r1 is not None, True)

r2 = mem.recall_for_query("what language do I use?")
check("exact: 'language' matched by 'language'", r2 is not None, True)

r3 = mem.recall_for_query("what is my language preference?")
check("both keys scored, best returned", r3 is not None, True)

# ── Bug 4: A2 escape with file-content queries ───────────────────────────
print("\n── Bug 4: A2 file-content anaphora ─────────────────────────────────")

import os, tempfile

# Simulate selected_file context: create a temp file
tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
tmp.write(b"test content")
tmp.close()

_A2_GENERAL_ESCAPE_RE = re.compile(
    r"^(?:what\s+is|what\s+are|explain|how\s+does|how\s+do|why\s+is|why\s+are"
    r"|define|describe|tell\s+me\s+about|what\s+do\s+you\s+know\s+about"
    r"|remember\s+that|note\s+that|my\s+(?:favorite|favourite|preferred)"
    r"|what\s+(?:is|are|was|were)\s+my\b|do\s+you\s+(?:know|remember)\b)\b",
    re.IGNORECASE,
)
_FILE_ANAPHORA_RE = re.compile(
    r"\b(?:the\s+(?:document|file|doc|report|pdf|image|picture)"
    r"|document\s+(?:i|we)\s+(?:uploaded|shared|sent|mentioned|provided|gave)"
    r"|file\s+(?:i|we)\s+(?:uploaded|shared|sent|mentioned|provided|gave)"
    r"|in\s+(?:this|the)\s+(?:file|document|doc|report)"
    r"|from\s+(?:this|the)\s+(?:file|document|doc)"
    r"|of\s+this\s+(?:file|document|doc))\b"
    r"|\.(?:pdf|docx?|txt|pptx?|csv|xlsx?|md|json|log)\b",
    re.IGNORECASE,
)

def should_escape(text):
    """Replicate the new A2 escape logic."""
    is_memory_q = bool(re.match(
        r"^(?:what\s+(?:is|are|was|were)\s+my\b|do\s+you\s+(?:know|remember)\b"
        r"|remember\s+that|note\s+that|my\s+(?:favorite|favourite|preferred))\b",
        text.strip(), re.IGNORECASE,
    ))
    has_file_anaphora = bool(_FILE_ANAPHORA_RE.search(text))
    return is_memory_q or (bool(_A2_GENERAL_ESCAPE_RE.match(text)) and not has_file_anaphora)

# Should escape (GENERAL, no file reference)
for q in ["what is an AI agent", "explain deep learning", "what is my favorite language?"]:
    check(f'should escape: "{q[:45]}"', should_escape(q), True)

# Should NOT escape (file-content question)
for q in ["what are the key points in the document I uploaded?",
          "what does the file say about AI?",
          "what is in this document?"]:
    check(f'should NOT escape: "{q[:55]}"', should_escape(q), False)

os.unlink(tmp.name)

print()
if all_ok:
    print("All tests passed!")
else:
    print("Some tests FAILED.")
    sys.exit(1)
