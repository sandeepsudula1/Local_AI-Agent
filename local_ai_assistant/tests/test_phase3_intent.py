"""
Test Phase 3: Intent Classifier with LLM-based context inference
Tests that the intent classifier properly handles conversation history
and email-aware classification rules
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.intent_classifier import IntentClassifier


def test_intent_classifier_instance():
    """Test that intent classifier can be instantiated"""
    classifier = IntentClassifier()
    assert classifier is not None, "IntentClassifier should instantiate"
    print("PASS: test_intent_classifier_instance")


def test_email_detection_with_keywords():
    """Test that email intents are detected with keywords"""
    classifier = IntentClassifier()
    
    # Test EMAIL_SEARCH
    result = classifier.classify("search for emails from alice", history=[])
    assert "EMAIL" in result or "search" in result.lower(), f"Should detect email search, got: {result}"
    print(f"PASS: test_email_detection_with_keywords (detected: {result})")


def test_history_format():
    """Test that history is passed in correct structured format"""
    classifier = IntentClassifier()
    
    # Structured history with role/content dicts
    history = [
        {"role": "user", "content": "search emails from alice"},
        {"role": "assistant", "content": "[Email search results...]"},
        {"role": "user", "content": "reply to that"},
    ]
    
    # This should work without errors
    try:
        result = classifier.classify("send the reply", history=history)
        assert result is not None, "Should return a valid intent"
        print(f"PASS: test_history_format (with {len(history)} history items)")
    except Exception as e:
        print(f"FAIL: test_history_format - {e}")
        raise


def test_empty_history():
    """Test that classifier works with empty history"""
    classifier = IntentClassifier()
    
    result = classifier.classify("search my emails", history=[])
    assert result is not None, "Should return intent even with empty history"
    print(f"PASS: test_empty_history (detected: {result})")


def test_system_prompt_contains_email_rules():
    """Test that system prompt contains the new email-aware rules"""
    import core.intent_classifier as ic_module
    system_prompt = ic_module._CONTEXT_SYSTEM_PROMPT
    
    # Check for key markers of the new email-aware prompt
    checks = [
        ("EMAIL_REPLY" in system_prompt, "EMAIL_REPLY section"),
        ("EMAIL_SEND" in system_prompt, "EMAIL_SEND section"),
        ("EMAIL_SEARCH" in system_prompt, "EMAIL_SEARCH section"),
        ("CONTEXT AWARENESS" in system_prompt or "context" in system_prompt.lower(), "Context awareness"),
        ("FOLLOW-UP" in system_prompt, "Follow-up inference"),
    ]
    
    passed = 0
    for check, description in checks:
        if check:
            passed += 1
            print(f"  Pass: System prompt contains: {description}")
        else:
            print(f"  Fail: System prompt missing: {description}")
    
    assert passed >= 3, f"System prompt should contain email-aware rules (got {passed}/5)"
    print(f"PASS: test_system_prompt_contains_email_rules ({passed}/5 checks)")


if __name__ == '__main__':
    print("\n" + "="*70)
    print("PHASE 3: INTENT CLASSIFIER LLM-BASED CONTEXT TESTS")
    print("="*70 + "\n")
    
    try:
        test_intent_classifier_instance()
        test_system_prompt_contains_email_rules()
        test_empty_history()
        test_history_format()
        test_email_detection_with_keywords()
        
        print("\n" + "="*70)
        print("ALL INTENT CLASSIFIER TESTS PASSED!")
        print("="*70)
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
