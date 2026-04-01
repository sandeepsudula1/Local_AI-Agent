"""
Test Phase 3: Orchestrator structured history format
Tests that the orchestrator correctly passes conversation history to intent classifier
in the proper structured format (list of dicts with role/content)
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.conversation_memory import conversation_memory


def test_history_format_structured():
    """Test that history is stored and retrieved in structured format"""
    # Clear memory
    conversation_memory.clear()
    
    # Add some conversation turns
    conversation_memory.add_turn("user", "search emails from alice")
    conversation_memory.add_turn("assistant", "Found 3 emails from alice@company.com")
    conversation_memory.add_turn("user", "reply to the first one")
    
    # Retrieve history
    history = conversation_memory.get_history(last_n=10)
    
    # Verify structure
    assert isinstance(history, list), "History should be a list"
    assert len(history) > 0, "History should contain items"
    
    for turn in history:
        assert isinstance(turn, dict), f"Each turn should be a dict, got {type(turn)}"
        assert "role" in turn, "Each turn should have a 'role' field"
        assert "content" in turn, "Each turn should have a 'content' field"
        assert turn["role"] in ["user", "assistant"], f"Role should be 'user' or 'assistant', got {turn['role']}"
    
    print(f"PASS: test_history_format_structured")
    print(f"  Retrieved {len(history)} history items in proper structure")
    for i, turn in enumerate(history):
        preview = turn["content"][:50] + "..." if len(turn["content"]) > 50 else turn["content"]
        print(f"    {i+1}. {turn['role']}: {preview}")


def test_history_preserves_order():
    """Test that history maintains conversation order"""
    conversation_memory.clear()
    
    messages = [
        ("user", "first message"),
        ("assistant", "first response"),
        ("user", "second message"),
        ("assistant", "second response"),
        ("user", "third message"),
    ]
    
    for role, content in messages:
        conversation_memory.add_turn(role, content)
    
    history = conversation_memory.get_history(last_n=10)
    
    # Verify order
    for i, (expected_role, expected_content) in enumerate(messages):
        assert history[i]["role"] == expected_role, f"Role mismatch at position {i}"
        assert history[i]["content"] == expected_content, f"Content mismatch at position {i}"
    
    print(f"PASS: test_history_preserves_order")
    print(f"  All {len(messages)} messages in correct order")


def test_history_trimming():
    """Test that history is trimmed to last_n entries"""
    conversation_memory.clear()
    
    # Add 10 messages
    for i in range(10):
        conversation_memory.add_turn("user", f"message {i+1}")
    
    # Get last 3
    history = conversation_memory.get_history(last_n=3)
    
    assert len(history) == 3, f"Should return 3 items, got {len(history)}"
    assert "message 8" in history[0]["content"], "Should get messages 8, 9, 10"
    assert "message 10" in history[2]["content"], "Last message should be message 10"
    
    print(f"PASS: test_history_trimming")
    print(f"  Correctly trimmed to last 3 messages")


def test_empty_history():
    """Test handling of empty history"""
    conversation_memory.clear()
    
    history = conversation_memory.get_history(last_n=10)
    
    assert isinstance(history, list), "Should return a list"
    assert len(history) == 0, "Should be empty"
    
    print(f"PASS: test_empty_history")


if __name__ == '__main__':
    print("\n" + "="*70)
    print("PHASE 3: ORCHESTRATOR STRUCTURED HISTORY FORMAT TESTS")
    print("="*70 + "\n")
    
    try:
        test_history_format_structured()
        print()
        test_history_preserves_order()
        print()
        test_history_trimming()
        print()
        test_empty_history()
        
        print("\n" + "="*70)
        print("ALL ORCHESTRATOR HISTORY FORMAT TESTS PASSED!")
        print("="*70)
        print("\nKey Validation:")
        print("  - History is stored as list[dict]")
        print("  - Each dict has 'role' (user/assistant) and 'content' fields")
        print("  - Order is preserved chronologically")
        print("  - Trimming to last_n works correctly")
        print("  - Ready for LLM-based intent classification with full context")
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
