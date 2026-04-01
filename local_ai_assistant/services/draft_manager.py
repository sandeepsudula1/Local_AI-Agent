"""
services/draft_manager.py
========================
Email draft management with persistence.

Handles:
- Creating drafts from generated replies
- Storing drafts (in-memory + optional JSON persistence)
- Retrieving and managing latest draft
- Lifecycle: draft_created → user_reviews → user_confirms → sent

Usage::

    from services.draft_manager import draft_manager

    # Create draft
    draft = draft_manager.create_draft(
        to="alice@company.com",
        subject="Re: Project Update",
        body="Thank you for the update...",
        reply_to_email_id="12345",
        tone="professional"
    )
    # Returns: {"status": "draft_created", "to": "...", "subject": "...", "body": "..."}

    # Get latest draft
    draft = draft_manager.get_latest_draft()

    # Confirm and send (after user approval)
    success = draft_manager.mark_draft_sent(draft_id)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime
import threading

log = logging.getLogger(__name__)

# Path to persist drafts (ABSOLUTE PATH)
_PROJECT_ROOT = Path(__file__).parent.parent
DRAFTS_FILE = (_PROJECT_ROOT / "data" / "drafts.json").resolve()


@dataclass
class EmailDraft:
    """Represents an email draft."""
    draft_id: str  # Unique ID for draft
    to: str  # Recipient email
    subject: str  # Email subject
    body: str  # Email body (generated reply)
    reply_to_email_id: Optional[str] = None  # ID of email being replied to
    tone: str = "professional"  # Tone used for generation
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "draft"  # draft | confirmed | sent | discarded
    confirmation_timestamp: Optional[str] = None
    sent_timestamp: Optional[str] = None
    # Gmail API cross-reference (populated when a real Gmail draft is created)
    gmail_draft_id: Optional[str] = None
    gmail_draft_url: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> EmailDraft:
        """Create from dictionary."""
        return cls(**data)


class DraftManager:
    """Manages email drafts with optional persistence."""

    def __init__(self, persist_path: Optional[Path] = None):
        """
        Initialize draft manager.

        Parameters
        ----------
        persist_path : Path, optional
            Path to JSON file for persisting drafts. If None, only in-memory storage.
        """
        # Convert to absolute path if provided
        if persist_path:
            self.persist_path = Path(persist_path).resolve()
        else:
            self.persist_path = persist_path
        
        self._drafts: dict[str, EmailDraft] = {}  # draft_id → EmailDraft
        self._latest_draft_id: Optional[str] = None
        self._lock = threading.Lock()
        self._counter = 0

        # Log initialization
        log.info("[DRAFT_MANAGER] Initializing with persist_path: %s", self.persist_path)

        # Load existing drafts from disk
        if self.persist_path and self.persist_path.exists():
            log.info("[DRAFT_MANAGER] Loading existing drafts from: %s", self.persist_path)
            self._load_from_disk()
        elif self.persist_path:
            log.info("[DRAFT_MANAGER] Drafts file does not exist yet: %s", self.persist_path)

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to_email_id: Optional[str] = None,
        tone: str = "professional",
        gmail_draft_id: Optional[str] = None,
        gmail_draft_url: Optional[str] = None,
    ) -> dict:
        """
        Create a new draft email.

        Parameters
        ----------
        to : str
            Recipient email address
        subject : str
            Email subject
        body : str
            Email body (the generated reply)
        reply_to_email_id : str, optional
            ID of the email being replied to
        tone : str
            Tone used for generation (professional, friendly, casual, formal)

        Returns
        -------
        dict
            Status response:
            {
                "status": "draft_created",
                "draft_id": "draft_20260330_001",
                "to": "alice@company.com",
                "subject": "Re: Project Update",
                "body": "Thank you for the update...",
                "tone": "professional",
                "created_at": "2026-03-30T14:30:00",
                "next_action": "Review and say 'send it' or 'edit draft'"
            }
        """
        with self._lock:
            # Generate unique draft ID
            self._counter += 1
            draft_id = f"draft_{datetime.now().strftime('%Y%m%d')}_{self._counter:03d}"

            # Create draft object
            draft = EmailDraft(
                draft_id=draft_id,
                to=to,
                subject=subject,
                body=body,
                reply_to_email_id=reply_to_email_id,
                tone=tone,
                status="draft",
                gmail_draft_id=gmail_draft_id,
                gmail_draft_url=gmail_draft_url,
            )

            # Store in memory
            self._drafts[draft_id] = draft
            self._latest_draft_id = draft_id

            log.info(
                "[DRAFT_MANAGER] Draft created in memory: %s (to: %s, subject: %s)",
                draft_id,
                to[:30],
                subject[:50],
            )

            # Persist to disk
            log.info("[DRAFT_MANAGER] Persisting draft %s to disk...", draft_id)
            self._persist_to_disk()
            log.info("[DRAFT_MANAGER] Draft %s persistence completed", draft_id)

            return {
                "status": "draft_created",
                "draft_id": draft_id,
                "to": to,
                "subject": subject,
                "body": body,
                "tone": tone,
                "created_at": draft.created_at,
                "gmail_draft_id": gmail_draft_id,
                "gmail_draft_url": gmail_draft_url,
                "next_action": "Review and say 'send it' or edit the draft",
            }

    def get_latest_draft(self) -> Optional[EmailDraft]:
        """Get the most recently created draft."""
        with self._lock:
            if self._latest_draft_id and self._latest_draft_id in self._drafts:
                return self._drafts[self._latest_draft_id]
            return None

    def get_draft(self, draft_id: str) -> Optional[EmailDraft]:
        """Get draft by ID."""
        with self._lock:
            return self._drafts.get(draft_id)

    def get_all_drafts(self, status: Optional[str] = None) -> list[EmailDraft]:
        """
        Get all drafts, optionally filtered by status.

        Parameters
        ----------
        status : str, optional
            Filter by status: "draft", "confirmed", "sent", "discarded"

        Returns
        -------
        list[EmailDraft]
            List of drafts matching criteria
        """
        with self._lock:
            drafts = list(self._drafts.values())
            if status:
                drafts = [d for d in drafts if d.status == status]
            # Sort by creation time, newest first
            return sorted(drafts, key=lambda d: d.created_at, reverse=True)

    def confirm_draft(self, draft_id: Optional[str] = None) -> Optional[dict]:
        """
        Mark draft as confirmed by user (before sending).

        Parameters
        ----------
        draft_id : str, optional
            Draft ID to confirm. If None, uses latest draft.

        Returns
        -------
        dict or None
            {
                "status": "draft_confirmed",
                "draft_id": "draft_...",
                "message": "Ready to send...",
                "to": "alice@company.com",
                "subject": "Re: ...",
                "body": "..."
            }
        """
        with self._lock:
            if draft_id is None:
                draft_id = self._latest_draft_id

            draft = self._drafts.get(draft_id)
            if not draft:
                return None

            draft.status = "confirmed"
            draft.confirmation_timestamp = datetime.now().isoformat()
            draft.updated_at = datetime.now().isoformat()

            log.info("[DRAFT_MANAGER] Draft confirmed: %s (to: %s)", draft_id, draft.to)
            self._persist_to_disk()

            return {
                "status": "draft_confirmed",
                "draft_id": draft_id,
                "message": f"Draft confirmed. Ready to send to: {draft.to}",
                "to": draft.to,
                "subject": draft.subject,
                "body": draft.body,
            }

    def mark_draft_sent(
        self,
        draft_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Mark draft as sent after SMTP transmission succeeds.

        Parameters
        ----------
        draft_id : str, optional
            Draft ID to mark as sent. If None, uses latest draft.
        error_message : str, optional
            If provided, marks draft as "failed" with error message.

        Returns
        -------
        dict or None
            {
                "status": "draft_sent" | "draft_failed",
                "draft_id": "draft_...",
                "message": "Email sent successfully" | error message
            }
        """
        with self._lock:
            if draft_id is None:
                draft_id = self._latest_draft_id

            draft = self._drafts.get(draft_id)
            if not draft:
                return None

            if error_message:
                draft.status = "failed"
                log.error("[DRAFT_MANAGER] Draft failed: %s - %s", draft_id, error_message)
            else:
                draft.status = "sent"
                draft.sent_timestamp = datetime.now().isoformat()
                log.info("[DRAFT_MANAGER] Draft marked as sent: %s (to: %s)", draft_id, draft.to)

            draft.updated_at = datetime.now().isoformat()
            self._persist_to_disk()

            return {
                "status": draft.status,
                "draft_id": draft_id,
                "message": error_message or "Email sent successfully!",
                "to": draft.to,
                "subject": draft.subject,
            }

    def discard_draft(self, draft_id: Optional[str] = None) -> Optional[dict]:
        """
        Discard a draft.

        Parameters
        ----------
        draft_id : str, optional
            Draft to discard. If None, uses latest draft.

        Returns
        -------
        dict or None
            Status response
        """
        with self._lock:
            if draft_id is None:
                draft_id = self._latest_draft_id

            draft = self._drafts.get(draft_id)
            if not draft:
                return None

            draft.status = "discarded"
            log.info("[DRAFT_MANAGER] Draft discarded: %s (to: %s)", draft_id, draft.to)
            self._persist_to_disk()

            return {
                "status": "draft_discarded",
                "draft_id": draft_id,
                "message": "Draft has been discarded.",
            }

    def _persist_to_disk(self) -> None:
        """Persist drafts to JSON file."""
        if not self.persist_path:
            log.debug("[DRAFT_MANAGER] No persist_path configured - in-memory only")
            return

        try:
            path = Path(self.persist_path).resolve()  # Ensure absolute path
            log.info("[DRAFT_MANAGER] Writing %d draft(s) to: %s", len(self._drafts), path)
            
            # Create parent directory
            path.parent.mkdir(parents=True, exist_ok=True)
            log.debug("[DRAFT_MANAGER] Directory ensured: %s", path.parent)

            # Serialize drafts
            drafts_data = {
                draft_id: draft.to_dict() for draft_id, draft in self._drafts.items()
            }

            # Write to file
            with path.open("w", encoding="utf-8") as f:
                json.dump(drafts_data, f, indent=2)
            
            # Verify file was written
            if path.exists():
                file_size = path.stat().st_size
                log.info("[DRAFT_MANAGER] ✓ Drafts successfully persisted (%d bytes) to: %s", 
                        file_size, path)
            else:
                log.error("[DRAFT_MANAGER] ✗ File write failed - file does not exist: %s", path)
        except Exception as e:
            log.error("[DRAFT_MANAGER] ✗ PERSISTENCE ERROR: %s (persist_path=%s)", e, self.persist_path, exc_info=True)

    def _load_from_disk(self) -> None:
        """Load drafts from JSON file."""
        if not self.persist_path:
            log.debug("[DRAFT_MANAGER] No persist_path configured")
            return
        
        path = Path(self.persist_path).resolve()
        if not path.exists():
            log.debug("[DRAFT_MANAGER] Drafts file not found: %s", path)
            return

        try:
            log.info("[DRAFT_MANAGER] Loading drafts from: %s", path)
            with path.open("r", encoding="utf-8") as f:
                drafts_data = json.load(f)

            for draft_id, draft_dict in drafts_data.items():
                draft = EmailDraft.from_dict(draft_dict)
                self._drafts[draft_id] = draft

            # Set counter based on existing drafts
            if self._drafts:
                self._counter = max(
                    int(did.split("_")[-1]) for did in self._drafts.keys()
                )

            log.info("[DRAFT_MANAGER] ✓ Loaded %d draft(s) from disk. Counter set to: %d", 
                    len(self._drafts), self._counter)
        except Exception as e:
            log.error("[DRAFT_MANAGER] ✗ LOAD ERROR from %s: %s", path, e, exc_info=True)

    def clear_old_drafts(self, days: int = 7) -> int:
        """
        Clear drafts older than N days (not "draft" status).

        Parameters
        ----------
        days : int
            Age threshold in days

        Returns
        -------
        int
            Number of drafts cleared
        """
        from datetime import timedelta

        with self._lock:
            cutoff = datetime.now() - timedelta(days=days)
            to_delete = []

            for draft_id, draft in self._drafts.items():
                if draft.status != "draft":
                    created = datetime.fromisoformat(draft.created_at)
                    if created < cutoff:
                        to_delete.append(draft_id)

            for draft_id in to_delete:
                del self._drafts[draft_id]

            if to_delete:
                self._persist_to_disk()
                log.info("[DRAFT_MANAGER] Cleared %d old draft(s)", len(to_delete))

            return len(to_delete)


# Singleton instance
draft_manager = DraftManager(persist_path=DRAFTS_FILE)
