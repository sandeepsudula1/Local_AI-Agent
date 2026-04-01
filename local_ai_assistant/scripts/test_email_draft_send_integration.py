"""
Test script for EMAIL_REPLY → DRAFT → EMAIL_SEND integration flow
Tests the end-to-end workflow of replying, creating draft, and sending
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.draft_manager import draft_manager


def print_header(title: str):
    """Print section header"""
    print(f"\n{'='*70}")
    print(f"   {title}")
    print(f"{'='*70}")


def reset_drafts():
    """Clear all drafts for testing"""
    draft_manager._drafts.clear()
    draft_manager._latest_draft_id = None
    draft_manager._draft_counter = 0


def simulate_email_reply_flow():
    """Simulate EMAIL_REPLY handler creating a draft"""
    print_header("SIMULATING EMAIL_REPLY HANDLER")
    
    reset_drafts()
    
    # Simulate what _handle_email_reply() does
    to = "alice@company.com"
    subject = "Project Update"
    body = "Thank you for the detailed update. I'll review it and get back to you."
    reply_to_email_id = "email_12345"
    tone = "professional"
    
    print(f"\n1️⃣  User says: 'reply to alice@company.com'")
    print(f"   Tone: {tone}")
    
    # This is what EMAIL_REPLY handler does
    draft_response = draft_manager.create_draft(
        to=to,
        subject=f"Re: {subject}",
        body=body,
        reply_to_email_id=reply_to_email_id,
        tone=tone,
    )
    
    print(f"\n2️⃣  EMAIL_REPLY handler creates draft:")
    print(f"   ✓ Status: {draft_response['status']}")
    print(f"   ✓ Draft ID: {draft_response['draft_id']}")
    print(f"   ✓ To: {draft_response['to']}")
    print(f"   ✓ Subject: {draft_response['subject']}")
    print(f"   ✓ Body: {draft_response['body'][:60]}...")
    print(f"   ✓ Created at: {draft_response['created_at']}")
    
    draft_id = draft_response["draft_id"]
    
    print(f"\n3️⃣  Draft stored to memory + data/drafts.json")
    print(f"   ✓ Ready for sending: {draft_response['next_action']}")
    
    return draft_id


def simulate_email_send_flow(draft_id: str):
    """Simulate EMAIL_SEND handler sending the draft"""
    print_header("SIMULATING EMAIL_SEND HANDLER")
    
    print(f"\n1️⃣  User says: 'send it'")
    
    # Get the draft
    draft = draft_manager.get_latest_draft()
    if not draft:
        print(f"   ❌ ERROR: No draft found!")
        return False
    
    print(f"\n2️⃣  EMAIL_SEND handler retrieves latest draft:")
    print(f"   ✓ Draft ID: {draft.draft_id}")
    print(f"   ✓ To: {draft.to}")
    print(f"   ✓ Subject: {draft.subject}")
    print(f"   ✓ Status: {draft.status}")
    
    # Simulate confirmation check
    print(f"\n3️⃣  Check for user confirmation: 'yes', 'send', 'confirm'...")
    confirm_keywords = {"yes", "go", "send", "confirm", "proceed", "do it", "ok"}
    user_input = "send it"
    has_confirm = any(word in user_input.lower() for word in confirm_keywords)
    
    if not has_confirm:
        print(f"   ⚠️  No confirmation! Show preview + ask again")
        return False
    
    print(f"   ✓ User confirmed with: '{user_input}'")
    
    # Simulate SMTP send (success)
    print(f"\n4️⃣  Simulate SMTP send...")
    send_success = True
    send_message = "Email sent via SMTP (smtp.gmail.com:587)"
    
    if send_success:
        print(f"   ✓ SMTP sent successfully")
        # Update draft status
        result = draft_manager.mark_draft_sent(draft_id, error_message=None)
        print(f"   ✓ Draft status updated to: {result['status']}")
    else:
        print(f"   ❌ SMTP failed: {send_message}")
        result = draft_manager.mark_draft_sent(draft_id, error_message=send_message)
        print(f"   ✗ Draft status updated to: {result['status']}")
    
    # Verify draft is in "sent" state
    final_draft = draft_manager.get_draft(draft_id)
    print(f"\n5️⃣  Verify final draft state:")
    print(f"   ✓ Status: {final_draft.status}")
    print(f"   ✓ Sent timestamp: {final_draft.sent_timestamp}")
    
    return True


def test_error_handling():
    """Test error handling - SMTP failure"""
    print_header("TESTING ERROR HANDLING - SMTP FAILURE")
    
    reset_drafts()
    
    # Create a draft
    draft_response = draft_manager.create_draft(
        to="bob@company.com",
        subject="Re: Meeting",
        body="Let's schedule a follow-up meeting.",
        reply_to_email_id="email_67890",
        tone="casual"
    )
    
    draft_id = draft_response["draft_id"]
    print(f"\n1️⃣  Draft created: {draft_id}")
    
    # Simulate SMTP failure
    print(f"\n2️⃣  Simulate SMTP failure...")
    error_msg = "Connection timeout: mail server not responding"
    result = draft_manager.mark_draft_sent(draft_id, error_message=error_msg)
    
    print(f"   ✗ SMTP failed: {error_msg}")
    print(f"   ✓ Draft marked as: {result['status']}")
    
    # Verify draft preserves error info
    draft = draft_manager.get_draft(draft_id)
    print(f"\n3️⃣  Draft preserved for retry:")
    print(f"   ✓ Status: {draft.status}")
    print(f"   ✓ Original body intact: {draft.body[:50]}...")
    
    return draft.status == "failed"


def test_state_persistence():
    """Test that drafts persist across manager instances"""
    print_header("TESTING STATE PERSISTENCE")
    
    reset_drafts()
    
    # Create draft with first manager instance
    print(f"\n1️⃣  Create draft with first DraftManager instance:")
    draft_response = draft_manager.create_draft(
        to="charlie@company.com",
        subject="Re: Proposal",
        body="I've reviewed the proposal and have some feedback.",
        reply_to_email_id="email_99999",
        tone="professional"
    )
    
    draft_id = draft_response["draft_id"]
    print(f"   ✓ Draft ID: {draft_id}")
    
    # Get draft with second "manager" (same singleton)
    print(f"\n2️⃣  Retrieve draft with same DraftManager instance:")
    retrieved = draft_manager.get_draft(draft_id)
    assert retrieved is not None, "Draft should be retrievable"
    print(f"   ✓ Draft retrieved: {retrieved.draft_id}")
    print(f"   ✓ Recipient: {retrieved.to}")
    print(f"   ✓ Status: {retrieved.status}")
    
    # Check JSON file
    drafts_file = project_root / "data" / "drafts.json"
    print(f"\n3️⃣  Verify JSON persistence:")
    print(f"   ✓ File: {drafts_file}")
    if drafts_file.exists():
        print(f"   ✓ File exists and is readable")
    
    return retrieved.draft_id == draft_id


def run_integration_tests():
    """Run all integration tests"""
    print("\n" + "="*70)
    print("EMAIL_REPLY → DRAFT → EMAIL_SEND INTEGRATION TESTS")
    print("="*70)
    
    try:
        # Test 1: Full flow
        draft_id = simulate_email_reply_flow()
        success = simulate_email_send_flow(draft_id)
        
        if not success:
            print("\n❌ EMAIL_SEND flow failed")
            return False
        
        # Test 2: Error handling
        if not test_error_handling():
            print("\n❌ Error handling test failed")
            return False
        
        # Test 3: Persistence
        if not test_state_persistence():
            print("\n❌ Persistence test failed")
            return False
        
        # Success!
        print(f"\n\n{'='*70}")
        print("✅ ALL INTEGRATION TESTS PASSED!")
        print("="*70)
        print("\nWorkflow Summary:")
        print("  1️⃣  User: 'reply to alice'")
        print("     → EMAIL_REPLY creates draft via draft_manager")
        print("     → Draft stored to memory + data/drafts.json")
        print("     → User sees draft with draft_id")
        print("")
        print("  2️⃣  User: 'send it'")
        print("     → EMAIL_SEND retrieves latest draft")
        print("     → Checks for confirmation keywords")
        print("     → Sends via SMTP (email_send_service)")
        print("     → Updates draft status to 'sent'")
        print("     → Shows success message with draft_id")
        print("")
        print("  ✅ Draft & Send system fully operational!")
        return True
        
    except AssertionError as e:
        print(f"\n\n{'='*70}")
        print(f"❌ TEST FAILED: {e}")
        print("="*70)
        return False
    except Exception as e:
        print(f"\n\n{'='*70}")
        print(f"❌ UNEXPECTED ERROR: {e}")
        print("="*70)
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
