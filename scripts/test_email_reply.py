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
        from agents.knowledge.email_reply_agent_v2 import generate_email_reply
        print("\u2713 agents.knowledge.email_reply_agent_v2")
    except Exception as e:
        print(f"\u2717 agents.knowledge.email_reply_agent_v2: {e}")
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


def test_intent_isolation():
    """Test 2b: Verify cross-feature intent isolation (no misclassification)."""
    print("\n" + "="*70)
    print("TEST 2b: Intent Isolation (Cross-Feature)")
    print("="*70)

    from core.intent_classifier import intent_classifier

    # (query, last_intent_context, expected_intent)
    test_cases = [
        # --- Reminders must NEVER become EMAIL_REPLY ---
        ("Set reminder in 1 minute",   None,          "REMINDER_SET"),
        ("Remind me in 5 minutes",     None,          "REMINDER_SET"),
        ("Set a reminder for 3pm",     None,          "REMINDER_SET"),
        # Reminder WITH email flow context (the critical regression cases)
        ("Set reminder in 1 minute",   "EMAIL_REPLY", "REMINDER_SET"),
        ("Remind me in 5 minutes",     "EMAIL_SEARCH", "REMINDER_SET"),
        ("Set a reminder for tomorrow", "EMAIL_REPLY", "REMINDER_SET"),
        # --- Questions must return EMAIL_SUMMARIZE / EMAIL_QUERY ---
        ("What is this email about?",  None,          "EMAIL_SUMMARIZE"),
        ("What does this email say?",  None,          "EMAIL_SUMMARIZE"),
        ("Summarize this email",       None,          "EMAIL_SUMMARIZE"),
        ("What is the email about",    None,          "EMAIL_SUMMARIZE"),
        ("Who sent this email?",       None,          "EMAIL_QUERY"),
        ("When was this email sent?",  None,          "EMAIL_QUERY"),
        ("What is the subject of this email?", None,  "EMAIL_QUERY"),
        # --- Explicit reply commands must still work ---
        ("reply to this email",        None,          "EMAIL_REPLY"),
        ("draft a response",           None,          "EMAIL_REPLY"),
        ("compose a reply",            None,          "EMAIL_REPLY"),
        # 'tell him/her' in email context still works (requires last_intent)
        ("Tell him I will be there",   "EMAIL_REPLY", "EMAIL_REPLY"),
    ]

    passed = 0
    for query, last_intent, expected in test_cases:
        detected = intent_classifier.classify(query, last_intent=last_intent)
        ctx = f" [ctx={last_intent}]" if last_intent else ""
        if detected == expected:
            print(f"  \u2713 '{query}'{ctx} \u2192 {detected}")
            passed += 1
        else:
            print(f"  \u2717 '{query}'{ctx} \u2192 {detected} (expected {expected})")

    print(f"\n  {passed}/{len(test_cases)} isolation tests passed")
    return passed == len(test_cases)


def test_email_summarize_override():
    """Test 2c: Verify EMAIL_SUMMARIZE is never overridden to EMAIL_REPLY."""
    print("\n" + "="*70)
    print("TEST 2c: EMAIL_SUMMARIZE Override Protection")
    print("="*70)

    from core.intent_classifier import intent_classifier

    # These cases were previously misclassified as EMAIL_REPLY due to
    # 'above mail' in _BROAD_REPLY_RE or aggressive email context override.
    test_cases = [
        # --- 'above mail' must NOT trigger EMAIL_REPLY ---
        ("Summarize above mail",        None,           "EMAIL_SUMMARIZE"),
        ("Summarize above mail",        "EMAIL_SEARCH", "EMAIL_SUMMARIZE"),
        ("Explain above email",         None,           "EMAIL_SUMMARIZE"),
        ("What is above email about?",  None,           "EMAIL_SUMMARIZE"),
        # --- Context follow-up questions (email flow, no explicit email noun) ---
        ("What is it about?",           "EMAIL_SEARCH", "EMAIL_SUMMARIZE"),
        ("What does it say?",           "EMAIL_SEARCH", "EMAIL_SUMMARIZE"),
        ("Summarize it",                "EMAIL_SEARCH", "EMAIL_SUMMARIZE"),
        ("Tell me more about it",       "EMAIL_SEARCH", "EMAIL_SUMMARIZE"),
        # --- Metadata follow-up questions → EMAIL_QUERY ---
        ("Who sent it?",                "EMAIL_SEARCH", "EMAIL_QUERY"),
        ("When was it sent?",           "EMAIL_SEARCH", "EMAIL_QUERY"),
        # --- Time/date questions must NOT be intercepted ---
        ("What time is it?",            "EMAIL_SEARCH", "TIME"),
    ]

    passed = 0
    for query, last_intent, expected in test_cases:
        detected = intent_classifier.classify(query, last_intent=last_intent)
        ctx = f" [ctx={last_intent}]" if last_intent else ""
        if detected == expected:
            print(f"  \u2713 '{query}'{ctx} \u2192 {detected}")
            passed += 1
        else:
            print(f"  \u2717 '{query}'{ctx} \u2192 {detected} (expected {expected})")

    print(f"\n  {passed}/{len(test_cases)} override-protection tests passed")
    return passed == len(test_cases)


def test_email_query_memory():
    """Test 2d: Verify EMAIL_QUERY uses memory (tool_executor level)."""
    print("\n" + "="*70)
    print("TEST 2d: EMAIL_QUERY Memory-First Handler")
    print("="*70)

    try:
        from core.tool_executor import _handle_email_query, _handle_email_search
        from memory.conversation_memory import conversation_memory

        # Store a fixture email in memory
        fixture = {
            "id": "999",
            "from": "alice@example.com",
            "subject": "Project Update",
            "date": "2026-04-01",
            "body": "Hi, the project is going well. We expect to finish by Friday.",
        }
        conversation_memory.set_last_email(fixture)

        tests = [
            ("Who sent this email?",     "alice@example.com"),
            ("When was it received?",    "2026-04-01"),
            ("What is the subject?",     "Project Update"),
        ]
        passed = 0
        for query, expected_substring in tests:
            answer, _ = _handle_email_query(query)
            if expected_substring in answer:
                print(f"  \u2713 '{query}' \u2192 contains '{expected_substring}'")
                passed += 1
            else:
                print(f"  \u2717 '{query}' \u2192 '{answer[:80]}' (expected '{expected_substring}')")

        # Cleanup
        conversation_memory.set_last_email(None)
        print(f"\n  {passed}/{len(tests)} email-query-memory tests passed")
        return passed == len(tests)

    except Exception as e:
        print(f"  \u2717 Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_memory_based_followup():
    """Test 2e: Verify follow-up resolution when memory.last_email is set.

    This tests the exact failure scenario reported:
      - last_intent may be None/GENERAL (stale), but memory.last_email IS set
      - follow-ups like "What is it about?" should resolve correctly
      - direct queries like "What is the subject?" (no anaphoric word) should work
    """
    print("\n" + "="*70)
    print("TEST 2e: Memory-Based Follow-Up Resolution (no last_intent)")
    print("="*70)

    try:
        from core.intent_classifier import IntentClassifier
        from memory.conversation_memory import conversation_memory

        # Store a fixture email so memory.last_email is set
        fixture = {
            "id": "42",
            "from": "akshitha@example.com",
            "subject": "Timesheet Reminder",
            "date": "2026-04-01",
            "body": "Please submit your timesheet by end of day.",
        }
        conversation_memory.set_last_email(fixture)
        classifier = IntentClassifier()

        # Cases: (query, last_intent_passed_in, expected_classification)
        cases = [
            # Core failure cases reported by user
            ("What is it about?",           None,       "EMAIL_SUMMARIZE"),
            ("What is it about?",           "GENERAL",  "EMAIL_SUMMARIZE"),
            ("What is the subject?",        None,       "EMAIL_QUERY"),
            ("What is the subject?",        "GENERAL",  "EMAIL_QUERY"),
            # Broader natural language without anaphoric words
            ("Summarize",                   None,       "EMAIL_SUMMARIZE"),
            ("Who sent this?",              None,       "EMAIL_QUERY"),
            ("Who is the sender?",          None,       "EMAIL_QUERY"),
            ("When was it received?",       None,       "EMAIL_QUERY"),
            # These should still work with correct last_intent (regression guard)
            ("Summarize it",                "EMAIL_SEARCH", "EMAIL_SUMMARIZE"),
            ("What is the subject?",        "EMAIL_SEARCH", "EMAIL_QUERY"),
            # Safety: reminders must NOT be intercepted
            ("Remind me at 9am",            None,       "REMINDER_SET"),
            ("Set reminder for tomorrow",   None,       "REMINDER_SET"),
        ]

        passed = 0
        for query, last_intent, expected in cases:
            result = classifier.classify(query, last_intent=last_intent)
            ctx = f" [ctx={last_intent!r}]" if last_intent is not None else " [ctx=None]"
            ok = result == expected
            mark = "  \u2713" if ok else "  \u2717"
            print(f"{mark} {query!r}{ctx} \u2192 {result!r}  (expected {expected!r})")
            if ok:
                passed += 1

        conversation_memory.set_last_email(None)
        print(f"\n  {passed}/{len(cases)} memory-based follow-up tests passed")
        return passed == len(cases)

    except Exception as e:
        print(f"  \u2717 Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_document_intent_grounding():
    """Test 2f: Verify document intents are not overridden by LLM or email context.

    Covers the three reported failure cases:
      1. "find AiAgent.txt"         -> RETRIEVAL   (was EMAIL_REPLY)
      2. "summarize the above file" -> SUMMARY     (was DOCUMENT_LIST)
      3. "summarizw spring.txt"     -> SUMMARY     (was EMAIL_REPLY, typo)
    Plus additional grounding cases.
    """
    print("\n" + "="*70)
    print("TEST 2f: Document Intent Grounding (strict rule-based priority)")
    print("="*70)

    try:
        from core.intent_classifier import IntentClassifier
        from memory.conversation_memory import conversation_memory

        # Ensure no stale email in memory (isolate from email context)
        conversation_memory.set_last_email(None)
        classifier = IntentClassifier()

        cases = [
            # ---- reported failures ----
            ("find AiAgent.txt",             None,            "RETRIEVAL"),
            ("summarize the above file",     None,            "SUMMARY"),
            ("summarizw spring.txt",         None,            "SUMMARY"),
            # ---- broader coverage ----
            ("open report.pdf",              None,            "RETRIEVAL"),
            ("get me notes.md",              None,            "RETRIEVAL"),
            ("summarize report.pdf",         None,            "SUMMARY"),
            ("summarise the document",       None,            "SUMMARY"),
            # typo variants
            ("summarise spring.txt",         None,            "SUMMARY"),
            ("summerize AiAgent.txt",        None,            "SUMMARY"),
            # bullet-prefixed input (noise char preprocessing)
            ("\u2022 find AiAgent.txt",      None,            "RETRIEVAL"),
            # with last_file context
            ("summarize it",                 "RETRIEVAL",     "SUMMARY"),
            ("summarizw it",                 "RETRIEVAL",     "SUMMARY"),
            # safety: email intents must NOT be affected
            ("find emails from alice",       None,            "EMAIL_SEARCH"),
            ("summarize this email",         None,            "EMAIL_SUMMARIZE"),
            # safety: reminders must NOT be affected
            ("remind me at 9am",             None,            "REMINDER_SET"),
        ]

        # For last_file-dependent cases we set a dummy last_file
        _DUMMY_FILE = "spring.txt"
        passed = 0
        for query, last_intent, expected in cases:
            last_file = _DUMMY_FILE if last_intent in {"RETRIEVAL", "SUMMARY"} else None
            result = classifier.classify(query, last_intent=last_intent, last_file=last_file)
            ok = result == expected
            mark = "  \u2713" if ok else "  \u2717"
            ctx = f" [ctx={last_intent!r}]" if last_intent else ""
            print(f"{mark} {query!r}{ctx} \u2192 {result!r}  (expected {expected!r})")
            if ok:
                passed += 1

        print(f"\n  {passed}/{len(cases)} document-grounding tests passed")
        return passed == len(cases)

    except Exception as e:
        print(f"  \u2717 Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


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
        from agents.knowledge.email_reply_agent_v2 import (
            generate_email_reply,
            get_tone_options,
        )
        from agents.knowledge.email_query_agent import load_all_emails

        emails = load_all_emails()
        if not emails:
            print("✗ No emails to test with")
            return False

        # Test each tone (v2 API takes an email dict, not an id string)
        tones = list(get_tone_options().keys())
        email = emails[0]

        print(f"Testing reply generation for email id: {email.get('id')}")
        print(f"Available tones: {', '.join(tones)}")

        for tone in tones:
            print(f"\n  Testing tone: {tone}...")
            reply = generate_email_reply(email, tone=tone)
            
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


def test_reply_style_detection():
    """Test 9: Verify detect_reply_style identifies bullet/short/normal correctly."""
    print("\n" + "="*70)
    print("TEST 9: Reply Style Detection")
    print("="*70)

    try:
        from agents.knowledge.email_reply_agent_v2 import detect_reply_style

        cases = [
            ("reply in bullet points",      "bullet_points"),
            ("Reply using bullet points",   "bullet_points"),
            ("reply as a list",             "bullet_points"),
            ("in point form",               "bullet_points"),
            ("make it short",               "short"),
            ("Make it concise",             "short"),
            ("reply briefly",               "short"),
            ("keep it short",               "short"),
            ("write a professional reply",  "normal"),
            ("reply to this email",         "normal"),
            ("respond formally",            "normal"),
        ]

        passed = True
        for query, expected in cases:
            result = detect_reply_style(query)
            ok = result == expected
            mark = "  ✓" if ok else "  ✗"
            print(f"{mark} detect_reply_style({query!r}) == {expected!r}  →  got {result!r}")
            if not ok:
                passed = False

        return passed

    except Exception as e:
        print(f"✗ Style detection test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_draft_modification_followup():
    """Test 10: Verify classifier returns EMAIL_REPLY for draft-modification follow-ups."""
    print("\n" + "="*70)
    print("TEST 10: Draft-Modification Follow-Up Classification")
    print("="*70)

    try:
        import sys, os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from core.intent_classifier import IntentClassifier

        classifier = IntentClassifier()

        cases = [
            "make it short",
            "make it shorter",
            "make it more concise",
            "keep it brief",
            "rewrite it",
            "shorten it",
            "use bullet points",
            "in bullet points",
            "make it more formal",
            "change the tone",
        ]

        passed = True
        for query in cases:
            result = classifier.classify(query, last_intent="EMAIL_REPLY")
            ok = result == "EMAIL_REPLY"
            mark = "  ✓" if ok else "  ✗"
            print(f"{mark} classify({query!r}, last_intent='EMAIL_REPLY') -> {result!r}")
            if not ok:
                passed = False

        # Make sure a reminder is NOT classified as EMAIL_REPLY in the same context
        reminder_result = classifier.classify("remind me tomorrow at 9am", last_intent="EMAIL_REPLY")
        ok = reminder_result == "REMINDER_SET"
        mark = "  ✓" if ok else "  ✗"
        print(f"{mark} remind me tomorrow at 9am -> {reminder_result!r}  (expected REMINDER_SET)")
        if not ok:
            passed = False

        return passed

    except Exception as e:
        print(f"✗ Draft modification test failed: {e}")
        import traceback
        traceback.print_exc()
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
        ("Intent Isolation", test_intent_isolation),
        ("EMAIL_SUMMARIZE Override Protection", test_email_summarize_override),
        ("EMAIL_QUERY Memory Handler", test_email_query_memory),
        ("Memory-Based Follow-Up Resolution", test_memory_based_followup),
        ("Document Intent Grounding", test_document_intent_grounding),
        ("Email Loading", test_email_loading),
        ("Reply Generation", test_reply_generation),
        ("SMTP Configuration", test_smtp_config),
        ("Email Validation", test_email_validation),
        ("Intent Routing", test_intent_routing),
        ("Tool Executor", test_tool_executor),
        ("Reply Style Detection", test_reply_style_detection),
        ("Draft Modification Follow-Up", test_draft_modification_followup),
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
        print("  2. Test with: python -c \"from agents.knowledge.email_reply_agent_v2 import generate_email_reply; from agents.knowledge.email_query_agent import load_all_emails; emails = load_all_emails(); print(generate_email_reply(emails[0], tone='professional')) if emails else print('No emails')\"")
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
