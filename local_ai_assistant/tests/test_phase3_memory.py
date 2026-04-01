"""
Test Phase 3: Conversation memory email context tracking
Tests the new set_last_email() and get_last_email() methods
"""

import sys
import threading
from pathlib import Path

# Add parent directory to path so we can import local_ai_assistant modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.conversation_memory import conversation_memory


def test_set_and_get_last_email():
    """Test setting and retrieving last email"""
    test_email = {
        'from': 'alice@company.com',
        'to': 'bob@company.com',
        'subject': 'Project Update',
        'body': 'Here is the project update',
        'id': 'email_123'
    }
    
    conversation_memory.set_last_email(test_email)
    retrieved = conversation_memory.get_last_email()
    
    assert retrieved is not None, "Email should not be None"
    assert retrieved['from'] == 'alice@company.com', "From field should match"
    assert retrieved['id'] == 'email_123', "ID should match"
    print("PASS: test_set_and_get_last_email")


def test_set_none_email():
    """Test setting None clears the email"""
    conversation_memory.set_last_email(None)
    result = conversation_memory.get_last_email()
    
    assert result is None, "Should return None when no email is set"
    print("PASS: test_set_none_email")


def test_get_last_email_returns_copy():
    """Test that get_last_email returns a copy, not the original"""
    test_email = {
        'from': 'alice@company.com',
        'subject': 'Test',
        'id': 'email_copy_test'
    }
    
    conversation_memory.set_last_email(test_email)
    retrieved1 = conversation_memory.get_last_email()
    retrieved1['subject'] = 'Modified'  # Modify the retrieved copy
    
    retrieved2 = conversation_memory.get_last_email()
    
    assert retrieved2['subject'] == 'Test', "Original should not be modified"
    print("PASS: test_get_last_email_returns_copy")


def test_thread_safe_access():
    """Test that thread-safe access works"""
    results = []
    
    def set_email():
        conversation_memory.set_last_email({
            'from': 'thread@test.com',
            'id': 'thread_email_001'
        })
    
    def get_email():
        email = conversation_memory.get_last_email()
        if email:
            results.append(email['id'])
    
    threads = []
    for _ in range(5):
        t1 = threading.Thread(target=set_email)
        t2 = threading.Thread(target=get_email)
        threads.extend([t1, t2])
    
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
    
    assert len(results) > 0, "Should have retrieved at least one email"
    print(f"PASS: test_thread_safe_access (retrieved {len(results)} emails from {len(threads)} threads)")


if __name__ == '__main__':
    print("\n" + "="*70)
    print("PHASE 3: CONVERSATION MEMORY EMAIL CONTEXT TRACKING TESTS")
    print("="*70 + "\n")
    
    try:
        test_set_and_get_last_email()
        test_set_none_email()
        test_get_last_email_returns_copy()
        test_thread_safe_access()
        
        print("\n" + "="*70)
        print("ALL TESTS PASSED!")
        print("="*70)
    except AssertionError as e:
        print(f"FAILED: {e}")
        exit(1)
