#!/usr/bin/env python
"""Test draft persistence with detailed logging."""
import json
import sys
from pathlib import Path
import tempfile
import shutil

# Add project to path
_ROOT = Path(__file__).parent
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.logging_config import setup_logging
from configs.settings import settings
from services.draft_manager import DraftManager

# Setup logging
setup_logging(level="INFO", log_format=settings.log_format)

def test_persistence():
    """Test that drafts persist to disk correctly."""
    import logging
    log = logging.getLogger(__name__)
    
    # Create a temporary test directory
    test_dir = Path(tempfile.mkdtemp(prefix="draft_test_"))
    drafts_file = test_dir / "test_drafts.json"
    
    print(f"\n{'='*70}")
    print(f"TEST: Draft Persistence with Enhanced Logging")
    print(f"{'='*70}")
    print(f"Test directory: {test_dir}")
    print(f"Drafts file: {drafts_file}")
    print(f"{'='*70}\n")
    
    try:
        # TEST 1: Create draft and verify persistence
        print("TEST 1: Create draft and verify file persistence")
        print("-" * 70)
        dm = DraftManager(persist_path=drafts_file)
        
        response = dm.create_draft(
            to="alice@company.com",
            subject="Re: Project Update",
            body="Thank you for the update on the project status.",
            reply_to_email_id="email_001",
            tone="professional"
        )
        
        print(f"✓ Draft created: {response['draft_id']}")
        
        # Verify file exists
        if drafts_file.exists():
            file_size = drafts_file.stat().st_size
            print(f"✓ File exists: {drafts_file}")
            print(f"✓ File size: {file_size} bytes")
            
            # Read and verify contents
            with open(drafts_file, 'r') as f:
                data = json.load(f)
                num_drafts = len(data)
                print(f"✓ File contains {num_drafts} draft(s)")
                for draft_id, draft_data in data.items():
                    print(f"  - {draft_id}: to={draft_data['to']}, status={draft_data['status']}")
        else:
            print(f"✗ FILE MISSING: {drafts_file}")
            return False
        
        # TEST 2: Create multiple drafts
        print("\nTEST 2: Create multiple drafts and check persistence")
        print("-" * 70)
        
        response2 = dm.create_draft(
            to="bob@company.com",
            subject="Re: Meeting Minutes",
            body="Thanks for sending the meeting notes.",
            tone="friendly"
        )
        print(f"✓ Second draft created: {response2['draft_id']}")
        
        # Verify both drafts in file
        with open(drafts_file, 'r') as f:
            data = json.load(f)
            if len(data) == 2:
                print(f"✓ Both drafts in file ({len(data)} total)")
                for draft_id in data:
                    print(f"  - {draft_id}")
            else:
                print(f"✗ Expected 2 drafts, found {len(data)}")
                return False
        
        # TEST 3: Reload from disk (new instance)
        print("\nTEST 3: Load drafts from disk (new DraftManager instance)")
        print("-" * 70)
        
        dm2 = DraftManager(persist_path=drafts_file)
        all_drafts = dm2.get_all_drafts()
        
        print(f"✓ Loaded {len(all_drafts)} draft(s):")
        for draft in all_drafts:
            print(f"  - {draft.draft_id}: to={draft.to}, status={draft.status}")
        
        if len(all_drafts) != 2:
            print(f"✗ Expected 2 drafts from disk, got {len(all_drafts)}")
            return False
        
        # TEST 4: Confirm and persist status change
        print("\nTEST 4: Confirm draft and verify status persists")
        print("-" * 70)
        
        dm2.confirm_draft(all_drafts[0].draft_id)
        print(f"✓ Draft confirmed: {all_drafts[0].draft_id}")
        
        # Reload again to verify status persisted
        dm3 = DraftManager(persist_path=drafts_file)
        confirmed_draft = dm3.get_draft(all_drafts[0].draft_id)
        
        if confirmed_draft.status == "confirmed":
            print(f"✓ Status persisted: status={confirmed_draft.status}")
        else:
            print(f"✗ Status not persisted: expected 'confirmed', got '{confirmed_draft.status}'")
            return False
        
        # TEST 5: Mark as sent and verify
        print("\nTEST 5: Mark draft as sent and verify persistence")
        print("-" * 70)
        
        dm3.mark_draft_sent(all_drafts[1].draft_id)
        print(f"✓ Draft marked as sent: {all_drafts[1].draft_id}")
        
        # Final verification
        with open(drafts_file, 'r') as f:
            final_data = json.load(f)
            statuses = {did: d['status'] for did, d in final_data.items()}
            print(f"✓ Final statuses:")
            for draft_id, status in statuses.items():
                print(f"  - {draft_id}: {status}")
            
            if "confirmed" in statuses.values() and "sent" in statuses.values():
                print(f"✓ All status changes persisted correctly")
            else:
                print(f"✗ Status changes not persisted")
                return False
        
        print(f"\n{'='*70}")
        print("✓ ALL PERSISTENCE TESTS PASSED")
        print(f"{'='*70}\n")
        return True
        
    finally:
        # Cleanup
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"Cleaned up test directory: {test_dir}")

if __name__ == "__main__":
    success = test_persistence()
    sys.exit(0 if success else 1)
