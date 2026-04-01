#!/usr/bin/env python
"""Test intent classifier - focused on EMAIL_REPLY reliability."""
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
setup_logging(level="WARNING", log_format=settings.log_format)  # Suppress debug noise

def test_critical_scenarios():
    """Test the critical email scenarios from the specification."""
    
    print(f"\n{'='*70}")
    print("TEST: Email Intent Classification - Critical Scenarios")
    print(f"{'='*70}\n")
    
    test_cases = [
        # SEARCH scenarios
        ("find emails from alice", "EMAIL_SEARCH", "Search from sender"),
        ("search for invoices", "EMAIL_SEARCH", "Search by topic"),
        
        # REPLY scenarios - THE CRITICAL PATH
        ("reply to above mail", "EMAIL_REPLY", "CRITICAL: Reply to above"),
        ("respond to that email", "EMAIL_REPLY", "CRITICAL: Respond to that"),
        ("reply", "EMAIL_REPLY", "CRITICAL: Simple 'reply'"),
        ("respond", "EMAIL_REPLY", "CRITICAL: Simple 'respond'"),
        ("draft a reply", "EMAIL_REPLY", "Draft reply"),
        ("compose a response", "EMAIL_REPLY", "Compose response"),
        ("give reply to above", "EMAIL_REPLY", "Give reply context"),

        # Typo/variant REPLY scenarios (the failing cases from real usage)
        ("give reponce to akshitha for timesheet email", "EMAIL_REPLY", "CRITICAL: Typo reponce"),
        ("give repsond to above mail", "EMAIL_REPLY", "CRITICAL: Typo repsond to above"),
        ("give respond to that email", "EMAIL_REPLY", "Give respond to that"),
        ("give response to above mail", "EMAIL_REPLY", "Give response to above"),
        
         # SEND scenarios - with explicit object
        ("send it", "EMAIL_SEND", "Send it"),
        ("send the reply", "EMAIL_SEND", "Send the reply"),
        ("go ahead", "EMAIL_SEND", "Go ahead"),
        ("send the email", "EMAIL_SEND", "Send the email"),
        
        # Other intents
        ("hello", "GREETING", "Greeting"),
        ("which is better, python or java", "GENERAL", "General question"),
    ]
    
    passed = 0
    failed = 0
    failed_cases = []
    
    for user_input, expected, description in test_cases:
        result = intent_classifier.classify(user_input)
        is_pass = result == expected
        
        if is_pass:
            passed += 1
            marker = "[PASS]"
        else:
            failed += 1
            marker = "[FAIL]"
            failed_cases.append((description, user_input, expected, result))
        
        print(f"{marker:6} {description:35} => {result:20} (expected {expected})")
    
    # Print summary
    print(f"\n{'='*70}")
    total = passed + failed
    print(f"Results: {passed}/{total} PASSED")
    
    if failed > 0:
        print(f"\nFailed cases:")
        for desc, inp, exp, got in failed_cases:
            print(f"  - {desc}: '{inp}' -> got {got}, expected {exp}")
    
    print(f"{'='*70}\n")
    return failed == 0

if __name__ == "__main__":
    success = test_critical_scenarios()
    sys.exit(0 if success else 1)
