"""
Test script for Email Draft & Send Flow
Tests the complete draft creation, persistence, and sending workflow
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.draft_manager import DraftManager, EmailDraft


def print_step(step_num: int, description: str):
    """Print test step header"""
    print(f"\n{'='*70}")
    print(f"STEP {step_num}: {description}")
    print(f"{'='*70}")


def test_draft_creation():
    """Test 1: Create a new draft"""
    print_step(1, "Create a new draft")
    
    # Create a new DraftManager instance (temporary)
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create a draft
    draft_response = dm.create_draft(
        to="alice@company.com",
        subject="Re: Project Update",
        body="Thank you for the update. I'll review the details.",
        reply_to_email_id="email_123",
        tone="professional"
    )
    
    print(f"✓ Draft created successfully!")
    print(f"  Draft Response:")
    for key, value in draft_response.items():
        print(f"    {key}: {value}")
    
    assert draft_response["status"] == "draft_created", "Status should be draft_created"
    assert "draft_" in draft_response["draft_id"], "Draft ID should contain 'draft_'"
    assert draft_response["to"] == "alice@company.com", "Recipient should match"
    assert draft_response["subject"] == "Re: Project Update", "Subject should match"
    
    draft_id = draft_response["draft_id"]
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()
    
    return draft_id


def test_draft_persistence():
    """Test 2: Verify draft persists to JSON file"""
    print_step(2, "Verify draft persists to JSON")
    
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create draft
    draft_response = dm.create_draft(
        to="bob@company.com",
        subject="Re: Meeting Notes",
        body="Great meeting today. Let's schedule a follow-up.",
        reply_to_email_id="email_456",
        tone="casual"
    )
    
    draft_id = draft_response["draft_id"]
    
    # Verify file was created
    assert test_drafts_file.exists(), f"Draft file should exist at {test_drafts_file}"
    print(f"✓ Draft file created at: {test_drafts_file}")
    
    # Read and verify content
    with open(test_drafts_file, "r") as f:
        data = json.load(f)
    
    assert draft_id in data, f"Draft {draft_id} should be in file"
    draft_data = data[draft_id]
    
    print(f"✓ Draft persisted to JSON successfully!")
    print(f"  Stored data:")
    print(f"    draft_id: {draft_data['draft_id']}")
    print(f"    to: {draft_data['to']}")
    print(f"    status: {draft_data['status']}")
    print(f"    created_at: {draft_data['created_at']}")
    
    assert draft_data["status"] == "draft", "Status should be 'draft'"
    assert draft_data["to"] == "bob@company.com", "Recipient should match"
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()
    
    return draft_id


def test_draft_retrieval():
    """Test 3: Retrieve draft by ID and get latest draft"""
    import time
    print_step(3, "Retrieve draft by ID and get latest draft")
    
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create multiple drafts (with delay to ensure different timestamps)
    draft1 = dm.create_draft(
        to="alice@company.com",
        subject="Re: Project",
        body="First draft",
        reply_to_email_id="email_1",
        tone="professional"
    )
    time.sleep(0.1)  # Small delay to ensure different timestamps
    
    draft2 = dm.create_draft(
        to="bob@company.com",
        subject="Re: Meeting",
        body="Second draft",
        reply_to_email_id="email_2",
        tone="casual"
    )
    
    draft_id_1 = draft1["draft_id"]
    draft_id_2 = draft2["draft_id"]
    
    # Test get_draft()
    retrieved = dm.get_draft(draft_id_1)
    assert retrieved is not None, f"Should retrieve draft {draft_id_1}"
    assert retrieved.to == "alice@company.com", "Retrieved draft should match"
    print(f"✓ Retrieved draft by ID: {draft_id_1}")
    
    # Test get_latest_draft()
    latest = dm.get_latest_draft()
    assert latest is not None, "Should have a latest draft"
    assert latest.draft_id == draft_id_2, "Latest should be second draft"
    print(f"✓ Latest draft: {draft_id_2} (to: {latest.to})")
    
    # Test get_all_drafts()
    all_drafts = dm.get_all_drafts()
    assert len(all_drafts) == 2, "Should have 2 drafts"
    # Latest draft should be in the list (may be first or last depending on sort)
    draft_ids = [d.draft_id for d in all_drafts]
    assert draft_id_2 in draft_ids, "Latest draft should be in all_drafts"
    assert all_drafts[0].draft_id == draft_id_2, f"Latest should be first in list (got {draft_ids})"
    print(f"✓ Retrieved all drafts: {len(all_drafts)} total (order: {draft_ids})")
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()


def test_draft_lifecycle():
    """Test 4: Draft lifecycle - confirm and mark as sent"""
    print_step(4, "Draft lifecycle - confirm and mark as sent")
    
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create draft
    draft_response = dm.create_draft(
        to="alice@company.com",
        subject="Re: Project Update",
        body="Thank you for the update.",
        reply_to_email_id="email_123",
        tone="professional"
    )
    
    draft_id = draft_response["draft_id"]
    print(f"✓ Draft created: {draft_id}")
    
    # Confirm draft
    confirm_response = dm.confirm_draft(draft_id)
    assert confirm_response is not None, "Confirm should return response"
    assert confirm_response["status"] == "draft_confirmed", "Status should be confirmed"
    print(f"✓ Draft confirmed: {draft_id}")
    
    # Verify status changed
    draft = dm.get_draft(draft_id)
    assert draft.status == "confirmed", "Draft status should be 'confirmed'"
    assert draft.confirmation_timestamp is not None, "Should have confirmation_timestamp"
    print(f"  Status: {draft.status}")
    print(f"  Confirmation time: {draft.confirmation_timestamp}")
    
    # Mark as sent
    sent_response = dm.mark_draft_sent(draft_id, error_message=None)
    assert sent_response is not None, "Mark sent should return response"
    assert sent_response["status"] == "sent", "Status should be 'sent'"
    print(f"✓ Draft marked as sent: {draft_id}")
    
    # Verify final status
    draft = dm.get_draft(draft_id)
    assert draft.status == "sent", "Draft status should be 'sent'"
    assert draft.sent_timestamp is not None, "Should have sent_timestamp"
    print(f"  Status: {draft.status}")
    print(f"  Sent time: {draft.sent_timestamp}")
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()


def test_draft_failed_status():
    """Test 5: Mark draft as failed with error message"""
    print_step(5, "Mark draft as failed with error message")
    
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create draft
    draft_response = dm.create_draft(
        to="alice@company.com",
        subject="Re: Test",
        body="Test draft",
        reply_to_email_id="email_fail",
        tone="professional"
    )
    
    draft_id = draft_response["draft_id"]
    
    # Mark as failed with error
    error_msg = "SMTP connection timeout"
    failed_response = dm.mark_draft_sent(draft_id, error_message=error_msg)
    
    assert failed_response["status"] == "failed", "Status should be 'failed'"
    print(f"✓ Draft marked as failed: {draft_id}")
    print(f"  Error: {failed_response['message']}")
    
    # Verify draft has error
    draft = dm.get_draft(draft_id)
    assert draft.status == "failed", "Draft status should be 'failed'"
    print(f"  Status: {draft.status}")
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()


def test_filter_by_status():
    """Test 6: Filter drafts by status"""
    import time
    print_step(6, "Filter drafts by status")
    
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create multiple drafts with different statuses
    draft1 = dm.create_draft(
        to="alice@company.com",
        subject="Re: 1",
        body="Draft 1",
        reply_to_email_id="email_1",
        tone="professional"
    )
    time.sleep(0.05)
    
    draft2 = dm.create_draft(
        to="bob@company.com",
        subject="Re: 2",
        body="Draft 2",
        reply_to_email_id="email_2",
        tone="casual"
    )
    time.sleep(0.05)
    
    draft3 = dm.create_draft(
        to="charlie@company.com",
        subject="Re: 3",
        body="Draft 3",
        reply_to_email_id="email_3",
        tone="friendly"
    )
    
    # Confirm draft1
    dm.confirm_draft(draft1["draft_id"])
    
    # Send draft2
    dm.mark_draft_sent(draft2["draft_id"], error_message=None)
    
    # Get drafts by status
    draft_status = dm.get_all_drafts(status="draft")
    confirmed_status = dm.get_all_drafts(status="confirmed")
    sent_status = dm.get_all_drafts(status="sent")
    
    assert len(draft_status) == 1, "Should have 1 draft with status 'draft'"
    assert len(confirmed_status) == 1, "Should have 1 draft with status 'confirmed'"
    assert len(sent_status) == 1, "Should have 1 draft with status 'sent'"
    
    print(f"✓ Filtered by status:")
    print(f"  Draft: {len(draft_status)}")
    print(f"  Confirmed: {len(confirmed_status)}")
    print(f"  Sent: {len(sent_status)}")
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()


def test_discard_draft():
    """Test 7: Discard a draft"""
    print_step(7, "Discard a draft")
    
    test_drafts_file = project_root / "data" / "test_drafts_temp.json"
    dm = DraftManager(persist_path=test_drafts_file)
    
    # Create draft
    draft_response = dm.create_draft(
        to="alice@company.com",
        subject="Re: Test",
        body="Test draft",
        reply_to_email_id="email_discard",
        tone="professional"
    )
    
    draft_id = draft_response["draft_id"]
    
    # Discard draft
    discard_response = dm.discard_draft(draft_id)
    assert discard_response is not None, "Discard should return response"
    assert discard_response["status"] == "draft_discarded", "Status should be discarded"
    print(f"✓ Draft discarded: {draft_id}")
    
    # Verify status
    draft = dm.get_draft(draft_id)
    assert draft.status == "discarded", "Draft status should be 'discarded'"
    print(f"  Status: {draft.status}")
    
    # Cleanup
    if test_drafts_file.exists():
        test_drafts_file.unlink()


def run_all_tests():
    """Run all tests"""
    print("\n" + "="*70)
    print("EMAIL DRAFT & SEND FUNCTIONALITY TEST SUITE")
    print("="*70)
    
    try:
        test_draft_creation()
        test_draft_persistence()
        test_draft_retrieval()
        test_draft_lifecycle()
        test_draft_failed_status()
        test_filter_by_status()
        test_discard_draft()
        
        print(f"\n\n{'='*70}")
        print("✅ ALL TESTS PASSED!")
        print("="*70)
        print("\nSummary:")
        print("  ✓ Draft creation working")
        print("  ✓ Draft persistence (JSON) working")
        print("  ✓ Draft retrieval (by ID and latest) working")
        print("  ✓ Draft lifecycle (confirm → sent) working")
        print("  ✓ Failed draft status tracking working")
        print("  ✓ Status filtering working")
        print("  ✓ Draft discard/cancellation working")
        print("\n✅ EMAIL_REPLY → DRAFT → EMAIL_SEND flow ready!")
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
    success = run_all_tests()
    sys.exit(0 if success else 1)
