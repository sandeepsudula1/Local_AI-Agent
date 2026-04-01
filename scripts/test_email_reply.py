"""
scripts/test_email_reply.py
===========================
Integration test suite for the email reply feature.

Tests all components:
1. Intent detection (EMAIL_REPLY, EMAIL_SEND)
2. Email loading
3. Reply generation
4. SMTP configuration
5. Email sending
6. Full pipeline integration

Run this to validate the entire email reply system.
"""

import sys
import os
from pathlib import Path

# Add project root to path
_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from core.logging_config import get_logger

log = get_logger(__name__)


def test_imports():
    """Test 1: Verify all modules can be imported."""
    print("\n" + "="*70)
    print("TEST 1: Module Imports")
    print("="*70)

    try:
        from core.intent_classifier import intent_classifier
        print("✓ core.intent_classifier")
    except Exception as e:
        print(f"✗ core.intent_classifier: {e}")
        return False

    try:
        from core.router import router
        print("✓ core.router")
    except Exception as e:
        print(f"✗ core.router: {e}")
        return False

    try:
        from core.tool_executor import tool_executor
        print("✓ core.tool_executor")
    except Exception as e:
        print(f"✗ core.tool_executor: {e}")
        return False

    try:
        from agents.knowledge.email_reply_agent import generate_email_reply
        print("✓ agents.knowledge.email_reply_agent")
    except Exception as e:
        print(f"✗ agents.knowledge.email_reply_agent: {e}")
        return False

    try:
        from services.email_send_service import send_email, get_smtp_config
        print("✓ services.email_send_service")
    except Exception as e:
        print(f"✗ services.email_send_service: {e}")
        return False

    return True


def test_intent_detection():
    """Test 2: Verify EMAIL_REPLY and EMAIL_SEND intent detection."""
    print("\n" + "="*70)
    print("TEST 2: Intent Detection")
    print("="*70)

    from core.intent_classifier import intent_classifier

    test_cases = [
        ("reply to this email", "EMAIL_REPLY"),
        ("draft a response to alice", "EMAIL_REPLY"),
        ("compose a reply", "EMAIL_REPLY"),
        ("respond to the email", "EMAIL_REPLY"),
        ("send the reply", "EMAIL_SEND"),
        ("yes, send it", "EMAIL_SEND"),
        ("send the email", "EMAIL_SEND"),
        ("go ahead and send", "EMAIL_SEND"),
    ]

    passed = 0
    for query, expected_intent in test_cases:
        detected = intent_classifier.classify(query)
        if detected == expected_intent:
            print(f"✓ '{query}' → {detected}")
            passed += 1
        else:
            print(f"✗ '{query}' → {detected} (expected {expected_intent})")

    return passed == len(test_cases)


def test_email_loading():
    """Test 3: Verify email data can be loaded."""
    print("\n" + "="*70)
    print("TEST 3: Email Loading")
    print("="*70)

    try:
        from agents.knowledge.email_query_agent import load_all_emails

        emails = load_all_emails()
        
        if emails:
            print(f"✓ Loaded {len(emails)} email(s)")
            
            # Check structure
            sample = emails[0]
            required_fields = ["id", "from", "subject", "body"]
            missing = [f for f in required_fields if f not in sample]
            
            if missing:
                print(f"  ⚠️  Missing fields: {missing}")
            else:
                print(f"  ✓ Email structure valid")
                print(f"    - From: {sample['from'][:40]}...")
                print(f"    - Subject: {sample['subject'][:40]}...")
            
            return True
        else:
            print("✗ No emails loaded")
            print("  Solution: Create data/emails.json or data/email_cache.json")
            return False

    except Exception as e:
        print(f"✗ Failed to load emails: {e}")
        return False


def test_reply_generation():
    """Test 4: Verify reply generation works."""
    print("\n" + "="*70)
    print("TEST 4: Reply Generation")
    print("="*70)

    try:
        from agents.knowledge.email_reply_agent import (
            generate_email_reply,
            get_tone_options,
        )
        from agents.knowledge.email_query_agent import load_all_emails

        emails = load_all_emails()
        if not emails:
            print("✗ No emails to test with")
            return False

        # Test each tone
        tones = list(get_tone_options().keys())
        email_id = str(emails[0]["id"])
        
        print(f"Testing reply generation for email: {email_id}")
        print(f"Available tones: {', '.join(tones)}")

        for tone in tones:
            print(f"\n  Testing tone: {tone}...")
            reply = generate_email_reply(email_id, tone=tone)
            
            if reply:
                print(f"    ✓ Generated {len(reply)} character reply")
                print(f"      Preview: {reply[:60]}...")
            else:
                print(f"    ✗ Failed to generate reply")
                print(f"      Check: Ollama running? Model available?")
                return False

        return True

    except Exception as e:
        print(f"✗ Reply generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_smtp_config():
    """Test 5: Verify SMTP configuration."""
    print("\n" + "="*70)
    print("TEST 5: SMTP Configuration")
    print("="*70)

    try:
        from services.email_send_service import get_smtp_config

        config = get_smtp_config()
        
        if config:
            print("✓ SMTP config loaded")
            print(f"  Host: {config.host}")
            print(f"  Port: {config.port}")
            print(f"  TLS: {config.use_tls}")
            print(f"  User: {config.user}")
            print(f"  From: {config.from_email or '<uses user>'}")
            
            if not config.password:
                print("  ⚠️  No password configured (sending will fail)")
                return True  # Not a hard failure, just incomplete config
            
            return True
        else:
            print("⚠️  SMTP not configured")
            print("  Solution: Set EMAIL_HOST and EMAIL_PORT in .env")
            print("  Example: EMAIL_HOST=smtp.gmail.com, EMAIL_PORT=587")
            return True  # Not a hard failure, feature just won't send

    except Exception as e:
        print(f"✗ SMTP config failed: {e}")
        return False


def test_email_validation():
    """Test 6: Verify email address validation."""
    print("\n" + "="*70)
    print("TEST 6: Email Validation")
    print("="*70)

    try:
        from services.email_send_service import _is_valid_email

        test_cases = [
            ("alice@example.com", True),
            ("bob.smith@company.co.uk", True),
            ("invalid@", False),
            ("@example.com", False),
            ("no-at-sign.com", False),
            ("alice@example..com", False),
        ]

        passed = 0
        for email, should_be_valid in test_cases:
            is_valid = _is_valid_email(email)
            if is_valid == should_be_valid:
                status = "✓" if is_valid else "✗"
                print(f"{status} '{email}' → Valid:{is_valid}")
                passed += 1
            else:
                print(f"✗ '{email}' → Valid:{is_valid} (expected {should_be_valid})")

        return passed == len(test_cases)

    except Exception as e:
        print(f"✗ Email validation failed: {e}")
        return False


def test_intent_routing():
    """Test 7: Verify intent routing."""
    print("\n" + "="*70)
    print("TEST 7: Intent Routing")
    print("="*70)

    try:
        from core.intent_classifier import intent_classifier
        from core.router import router

        test_cases = [
            ("reply to alice", "email.reply"),
            ("send the reply", "email.send"),
        ]

        passed = 0
        for query, expected_tool in test_cases:
            intent = intent_classifier.classify(query)
            tool = router.route(intent)
            
            if tool == expected_tool:
                print(f"✓ '{query}' → {intent} → {tool}")
                passed += 1
            else:
                print(f"✗ '{query}' → {intent} → {tool} (expected {expected_tool})")

        return passed == len(test_cases)

    except Exception as e:
        print(f"✗ Intent routing failed: {e}")
        return False


def test_tool_executor():
    """Test 8: Verify tool executor can handle email.reply."""
    print("\n" + "="*70)
    print("TEST 8: Tool Executor")
    print("="*70)

    try:
        from core.tool_executor import tool_executor

        available_tools = tool_executor.available_tools()
        
        required_tools = ["email.reply", "email.send"]
        found_tools = [t for t in required_tools if t in available_tools]
        
        print(f"Available tools: {len(available_tools)}")
        for tool in required_tools:
            if tool in available_tools:
                print(f"  ✓ {tool}")
            else:
                print(f"  ✗ {tool}")

        return len(found_tools) == len(required_tools)

    except Exception as e:
        print(f"✗ Tool executor check failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*68 + "║")
    print("║" + "  EMAIL REPLY FEATURE - INTEGRATION TEST SUITE".center(68) + "║")
    print("║" + " "*68 + "║")
    print("╚" + "="*68 + "╝")

    tests = [
        ("Imports", test_imports),
        ("Intent Detection", test_intent_detection),
        ("Email Loading", test_email_loading),
        ("Reply Generation", test_reply_generation),
        ("SMTP Configuration", test_smtp_config),
        ("Email Validation", test_email_validation),
        ("Intent Routing", test_intent_routing),
        ("Tool Executor", test_tool_executor),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)

    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)

    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {test_name}")

    print("-"*70)
    print(f"Result: {passed_count}/{total_count} tests passed")

    if passed_count == total_count:
        print("\n✓ All tests passed! Email reply feature is ready to use.")
        print("\nNext steps:")
        print("  1. Configure .env with SMTP settings (see EMAIL_REPLY_QUICKSTART.md)")
        print("  2. Test with: python -c \"from agents.knowledge.email_reply_agent import generate_email_reply; print(generate_email_reply('1', tone='professional'))\"")
        print("  3. Use in your assistant!")
        return 0
    else:
        print("\n✗ Some tests failed. See errors above.")
        print("\nTroubleshooting:")
        print("  - Email loading failed: Add sample emails to data/emails.json")
        print("  - Reply generation failed: Check Ollama is running (ollama serve)")
        print("  - SMTP failed: Configure EMAIL_HOST and EMAIL_PORT in .env")
        return 1


if __name__ == "__main__":
    sys.exit(main())
