#!/usr/bin/env python
"""Fix multiple indentation issues in intent_classifier.py and add go_ahead pattern"""

path = 'core/intent_classifier.py'
with open(path, 'r') as f:
    content = f.read()

# Fix the for loop body: "                if re.search" -> "            if re.search"
# and "                    return" -> "                return"
old1 = '''        for pattern in send_patterns:
                if re.search(pattern, t, re.IGNORECASE):
                    return "EMAIL_SEND"'''

new1 = '''        for pattern in send_patterns:
            if re.search(pattern, t, re.IGNORECASE):
                return "EMAIL_SEND"'''

if old1 in content:
    content = content.replace(old1, new1, 1)
    print("Fixed for loop body indentation")
else:
    print("WARNING: Pattern not found for for loop fix!")
    # Try to find what's there
    idx = content.find('for pattern in send_patterns:')
    if idx >= 0:
        print(f"Found at position {idx}")
        print(repr(content[idx:idx+150]))

# Also add "go ahead" as a standalone match pattern
old2 = '''            r"\b(go ahead|let'?s proceed|send away)\b.{0,30}\b(with\s+)?(the\s+)?(email|reply|message|response)\b",
        ]'''

new2 = '''            r"\b(go ahead|let'?s proceed|send away)\b.{0,30}\b(with\s+)?(the\s+)?(email|reply|message|response)\b",
            # Standalone confirmations - match before CHAT "yeah/ok" to catch email confirmations
            r"^(go ahead|proceed|do it)[\s!.,]*$",
        ]'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    print("Added standalone 'go ahead' pattern")
else:
    print("WARNING: Could not find location to add go_ahead pattern")

with open(path, 'w') as f:
    f.write(content)

print("Done!")
