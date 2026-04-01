#!/usr/bin/env python3
"""
test_email_context_debug.py
============================
Debug test to validate EMAIL_SEARCH → EMAIL_REPLY context propagation.

This script:
1. Simulates an EMAIL_SEARCH
2. Verifies email is stored in memory
3. Simulates an EMAIL_REPLY
4. Verifies email is retrieved from memory
5. Shows all debug logs

Run: python test_email_context_debug.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from core.logging_config import get_logger
from memory.conversation_memory import conversation_memory
from pipelines.orchestrator import orchestrator
from core.intent_classifier import IntentClassifier

log = get_logger(__name__)

print("\n" + "="*80)
print("EMAIL CONTEXT PROPAGATION DEBUG TEST")
print("="*80)

# Clear memory
print("\n[SETUP] Clearing conversation memory...")
conversation_memory.clear()

# Create intent classifier
classifier = IntentClassifier()

# ============================================================================
# SCENARIO 1: EMAIL_SEARCH
# ============================================================================
print("\n" + "─"*80)
print("SCENARIO 1: USER SEARCHES EMAILS")
print("─"*80)

email_search_query = "search emails from alice"
print(f"\n[USER] {email_search_query}")

# Step 1: Classify intent
print("\n[DEBUG] Classifying intent...")
intent_1 = classifier.classify(email_search_query, history=[])
print(f"[RESULT] Intent: {intent_1}")

# Step 2: Add to conversation memory
print("\n[DEBUG] Adding to conversation history...")
conversation_memory.add_turn("user", email_search_query)

# Step 3: Simulate EMAIL_SEARCH handler (normally done by tool_executor)
print("\n[DEBUG] Simulating EMAIL_SEARCH handler...")
from core.tool_executor import _handle_email_search

try:
    # This would be called by tool_executor
    answer, _ = _handle_email_search(email_search_query)
    print(f"[EMAIL_SEARCH] Result (first 100 chars): {answer[:100] if answer else 'None'}...")
    
    # Add assistant response to memory
    conversation_memory.add_turn("assistant", answer[:200] if answer else "Search completed")
except Exception as e:
    log.error("EMAIL_SEARCH failed: %s", e)
    print(f"[ERROR] EMAIL_SEARCH failed: {e}")

# Check if last_email is stored
print("\n[DEBUG] Checking memory after EMAIL_SEARCH...")
last_email = conversation_memory.get_last_email()
if last_email:
    print(f"[SUCCESS] Last email found in memory!")
    print(f"  From: {last_email.get('from', '?')}")
    print(f"  Subject: {last_email.get('subject', '?')}")
else:
    print(f"[WARNING] NO last email in memory!")

# ============================================================================
# SCENARIO 2: CONVERSATION HISTORY CHECK
# ============================================================================
print("\n" + "─"*80)
print("SCENARIO 2: CHECK CONVERSATION HISTORY")
print("─"*80)

history = conversation_memory.get_history(last_n=10)
print(f"\n[DEBUG] History length: {len(history)}")
for i, turn in enumerate(history):
    role = turn.get('role', '?')
    content = turn.get('content', '')[:80]
    print(f"  {i+1}. {role}: {content}")

# ============================================================================
# SCENARIO 3: EMAIL_REPLY
# ============================================================================
print("\n" + "─"*80)
print("SCENARIO 3: USER REPLIES TO EMAIL")
print("─"*80)

email_reply_query = "give reply to above mail"
print(f"\n[USER] {email_reply_query}")

# Step 1: Get current history for context
history_for_classifier = conversation_memory.get_history(last_n=6)

# Step 2: Classify intent
print("\n[DEBUG] Classifying intent with history...")
intent_2 = classifier.classify(email_reply_query, history=history_for_classifier)
print(f"[RESULT] Intent: {intent_2}")

# Step 3: Add to conversation memory
print("\n[DEBUG] Adding to conversation history...")
conversation_memory.add_turn("user", email_reply_query)

# Check memory before EMAIL_REPLY
print("\n[DEBUG] Checking memory before EMAIL_REPLY...")
last_email_before = conversation_memory.get_last_email()
if last_email_before:
    print(f"[SUCCESS] Last email available: from={last_email_before.get('from', '?')}")
else:
    print(f"[WARNING] NO last email available!")

# Step 4: Simulate EMAIL_REPLY handler (would be called by tool_executor)
if intent_2 == "EMAIL_REPLY":
    print("\n[DEBUG] Simulating EMAIL_REPLY handler...")
    from core.tool_executor import _handle_email_reply
    
    try:
        answer, _ = _handle_email_reply(email_reply_query)
        print(f"[EMAIL_REPLY] Result (first 150 chars): {answer[:150] if answer else 'None'}...")
        
        # Add assistant response to memory
        conversation_memory.add_turn("assistant", answer[:200] if answer else "Reply created")
    except Exception as e:
        log.error("EMAIL_REPLY failed: %s", e)
        print(f"[ERROR] EMAIL_REPLY failed: {e}")
else:
    print(f"\n[ERROR] Expected EMAIL_REPLY but got: {intent_2}")
    print(f"[DEBUG] This means the intent classifier didn't recognize 'give reply to above mail' as EMAIL_REPLY")

# ============================================================================
# SCENARIO 4: FINAL VERIFICATION
# ============================================================================
print("\n" + "─"*80)
print("SCENARIO 4: FINAL VERIFICATION")
print("─"*80)

print("\n[VERIFICATION] Final memory state:")
final_history = conversation_memory.get_history(last_n=10)
print(f"  History items: {len(final_history)}")

final_email = conversation_memory.get_last_email()
print(f"  Last email: {'EXISTS' if final_email else 'NONE'}")
if final_email:
    print(f"    From: {final_email.get('from', '?')}")

# ============================================================================
# TEST SUMMARY
# ============================================================================
print("\n" + "="*80)
print("TEST SUMMARY")
print("="*80)

passed = []
failed = []

# Test 1: EMAIL_SEARCH intent classification
if intent_1 == "EMAIL_SEARCH":
    passed.append("EMAIL_SEARCH intent recognized")
else:
    failed.append(f"EMAIL_SEARCH intent NOT recognized (got {intent_1})")

# Test 2: Email stored after EMAIL_SEARCH
if last_email:
    passed.append("Email stored in memory after EMAIL_SEARCH")
else:
    failed.append("Email NOT stored in memory after EMAIL_SEARCH")

# Test 3: EMAIL_REPLY intent classification
if intent_2 == "EMAIL_REPLY":
    passed.append("EMAIL_REPLY intent recognized with context")
else:
    failed.append(f"EMAIL_REPLY intent NOT recognized (got {intent_2})")

# Test 4: Final email available
if final_email:
    passed.append("Email still available for EMAIL_REPLY")
else:
    failed.append("Email NOT available for EMAIL_REPLY")

print(f"\n✓ PASSED ({len(passed)}):")
for p in passed:
    print(f"  - {p}")

if failed:
    print(f"\n✗ FAILED ({len(failed)}):")
    for f in failed:
        print(f"  - {f}")
else:
    print(f"\n✅ ALL TESTS PASSED")

print("\n" + "="*80 + "\n")
