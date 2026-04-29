"""Quick smoke test for intent classifier fixes."""
import sys
sys.path.insert(0, ".")

from core.intent_classifier import IntentClassifier

fp = IntentClassifier._regex_fastpath

cases = [
    # (description, query, expected)
    ("MS1 - remember phrase",     "Remember that my favorite language is Python", "MEMORY_STORE"),
    ("MS2 - my favorite X is Y",  "My favorite editor is VS Code",                "MEMORY_STORE"),
    ("MS3 - I prefer",            "Note that I prefer dark mode",                 "MEMORY_STORE"),
    ("MR1 - what is my favorite", "What is my favorite language?",                "MEMORY_RECALL"),
    ("MR2 - do you know my name", "Do you know my name?",                         "MEMORY_RECALL"),
    ("MR3 - what's my pref",      "What's my preferred editor?",                  "MEMORY_RECALL"),
    ("II1 - gibberish",           "asdfghjkl",                                    "INVALID_INPUT"),
    ("II2 - too short",           "xz",                                           "INVALID_INPUT"),
    ("GEN1 - explain concept",    "Explain what an AI agent is",                  "GENERAL"),
]

all_ok = True
for desc, query, expected in cases:
    result = fp(query)
    status = "OK" if result == expected else "FAIL"
    if status == "FAIL":
        all_ok = False
    print(f"  [{status}] {desc}: got {result!r}, expected {expected!r}")

print()
if all_ok:
    print("All tests passed!")
else:
    print("Some tests FAILED.")
    sys.exit(1)
