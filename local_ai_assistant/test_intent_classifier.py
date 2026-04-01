#!/usr/bin/env python
"""Test intent classifier with focus on EMAIL_REPLY reliability."""
import sys
from pathlib import Path

# Add project to path
_ROOT = Path(__file__).parent
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.logging_config import setup_logging
from configs.settings import settings
from core.intent_classifier import intent_classifier

# Setup logging
setup_logging(level="INFO", log_format=settings.log_format)

def test_intent_classification():
    """Test intent classification with context scenarios."""
    import logging
    log = logging.getLogger(__name__)
    
    print(f"\n{'='*70}")
    print("TEST: Intent Classification Reliability")
    print(f"{'='*70}\n")
    
    # Test scenarios: (input, expected_intent, description)
    test_cases = [
        # EMAIL_SEARCH scenarios
        ("find emails from alice", "EMAIL_SEARCH", "Basic search"),
        ("search for emails about project", "EMAIL_SEARCH", "Search with topic"),
        ("show me recent emails", "EMAIL_SEARCH", "Show recent"),
        
        # EMAIL_REPLY scenarios (regex fast-path)
        ("reply to above mail", "EMAIL_REPLY", "Reply to above mail"),
        ("respond to that email", "EMAIL_REPLY", "Respond to that"),
        ("draft a reply", "EMAIL_REPLY", "Draft a reply"),
        ("reply", "EMAIL_REPLY", "Simple reply"),
        ("respond", "EMAIL_REPLY", "Simple respond"),
        ("compose a response", "EMAIL_REPLY", "Compose response"),
        ("write a reply back", "EMAIL_REPLY", "Write reply back"),
        ("give reply to above", "EMAIL_REPLY", "Give reply to above"),
        ("reply to this email", "EMAIL_REPLY", "Reply to this email"),
        ("respond to the mail", "EMAIL_REPLY", "Respond to the mail"),
        
        # EMAIL_SEND scenarios (confirmation)
        ("send it", "EMAIL_SEND", "Send it"),
        ("send the reply", "EMAIL_SEND", "Send the reply"),
        ("go ahead", "EMAIL_SEND", "Go ahead"),
        ("proceed", "EMAIL_SEND", "Proceed - just the word"),
        ("confirm", "EMAIL_SEND", "Confirm - just the word"),
        
        # Note: "yes" and "ok" alone are ambiguous without draft context
        # They could be CHAT or EMAIL_SEND depending on system state
        # Test them with history instead
        
        # GREETING scenarios
        ("hello", "GREETING", "Hello greeting"),
        ("hi there", "GREETING", "Hi there"),
        
        # GENERAL scenarios
        ("which is better, python or java", "GENERAL", "Question"),
    ]
    
    passed = 0
    failed = 0
    
    for user_input, expected_intent, description in test_cases:
        result = intent_classifier.classify(user_input)
        is_pass = result == expected_intent
        status = "[PASS]" if is_pass else "[FAIL]"
        
        if is_pass:
            passed += 1
            print(f"{status} {description:30} | '{user_input:40}' -> {result}")
        else:
            failed += 1
            print(f"{status} {description:30} | '{user_input:40}' -> {result} (expected {expected_intent})")
    
    # Test context-aware classification (with history)
    print(f"\n{'-'*70}")
    print("CONTEXT-AWARE TESTS (with conversation history)")
    print(f"{'-'*70}\n")
    
    context_tests = [
        (
            "reply to above mail",
            [
                {"role": "user", "content": "search emails from alice"},
                {"role": "assistant", "content": "Found 1 email from alice@company.com about project update"}
            ],
            "EMAIL_REPLY",
            "Reply after search (context)"
        ),
        (
            "respond to that",
            [
                {"role": "user", "content": "find emails about invoice"},
                {"role": "assistant", "content": "Found 3 emails about invoices"}
            ],
            "EMAIL_REPLY",
            "Respond after search (minimal)"
        ),
        (
            "yes send it",
            [
                {"role": "user", "content": "reply to alice"},
                {"role": "assistant", "content": "Draft created..."}
            ],
            "EMAIL_SEND",
            "Send confirmation (after draft)"
        ),
    ]
    
    for user_input, history, expected, desc in context_tests:
        result = intent_classifier.classify(user_input, history=history)
        is_pass = result == expected
        status = "[PASS]" if is_pass else "[FAIL]"
        
        if is_pass:
            passed += 1
            print(f"{status} {desc:40} -> {result}")
        else:
            failed += 1
            print(f"{status} {desc:40} -> {result} (expected {expected})")
    
    # Summary
    total = passed + failed
    print(f"\n{'='*70}")
    print(f"RESULTS: {passed}/{total} tests passed")
    if failed == 0:
        print("[SUCCESS] ALL TESTS PASSED")
    else:
        print(f"[FAILURE] {failed} test(s) failed")
    print(f"{'='*70}\n")
    
    return failed == 0

if __name__ == "__main__":
    success = test_intent_classification()
    sys.exit(0 if success else 1)
