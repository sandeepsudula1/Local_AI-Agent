#!/usr/bin/env python
"""Quick script to fix indentation in intent_classifier.py"""
import re

path = 'core/intent_classifier.py'
with open(path, 'r') as f:
    lines = f.readlines()

# Find and fix the problem line
for i, line in enumerate(lines):
    # Line 324 has extra spaces before "for pattern in send_patterns:"
    if 'for pattern in send_patterns:' in line and line.startswith('            '):
        lines[i] = line.replace('            for', '        for', 1)
        print(f"Fixed line {i+1}")

with open(path, 'w') as f:
    f.writelines(lines)

print("Done!")
