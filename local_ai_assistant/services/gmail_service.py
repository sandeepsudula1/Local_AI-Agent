"""
services/gmail_service.py
=========================
Gmail API integration for creating real email drafts.

Authentication
--------------
Uses OAuth 2.0 "Installed App" flow.  One-time setup:
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable Gmail API → Create OAuth2 credentials
     (Application type: Desktop app / Installed application)
  3. Download the JSON and save it as:
       <project_root>/data/credentials.json
  4. First time the assistant creates a draft, a browser window will open
     so you can authorise access. The resulting token is stored at:
       <project_root>/data/gmail_token.json
     and reused automatically from then on.

Scope
-----
  https://www.googleapis.com/auth/gmail.compose
  (compose-only — cannot read or delete existing mail)

Usage::

    from services.gmail_service import gmail_service

    result = gmail_service.create_draft(
        to="alice@example.com",
        subject="Re: Project Update",
        body="Hi Alice, thanks for the update...",
        thread_id="...",          # optional — threads the draft in Gmail
        in_reply_to_msg_id="...", # optional — populates In-Reply-To / References
    )
    # Returns: {"success": True, "draft_id": "r123...", "message": "Draft created in Gmail"}
"""

from __future__ import annotations

import base64
import logging
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Startup diagnostics — run at import time so mismatches are visible immediately
# ---------------------------------------------------------------------------
_in_venv: bool = sys.prefix != sys.base_prefix
_venv_label: str = f"venv ({sys.prefix})" if _in_venv else f"system Python ({sys.prefix})"
log.info("[GMAIL] Module loaded — interpreter: %s  [%s]", sys.executable, _venv_label)

if not _in_venv:
    log.warning(
        "[GMAIL] No virtual environment is active. Running on system Python.\n"
        "  Interpreter: %s\n"
        "  If Gmail libraries are missing, activate the project venv first, then:\n"
        "    %s -m pip install google-auth google-auth-oauthlib google-api-python-client",
        sys.executable,
        sys.executable,
    )

try:
    import google.oauth2  # noqa: F401
    log.info("[GMAIL] google.oauth2 import OK")
except ImportError as _gmail_import_err:
    _fix_cmd = f"{sys.executable} -m pip install google-auth google-auth-oauthlib google-api-python-client"
    log.error(
        "[GMAIL] STARTUP IMPORT FAILED — google.oauth2 not found in active environment.\n"
        "  Error      : %s\n"
        "  Interpreter: %s\n"
        "  In venv    : %s\n"
        "  Fix        : %s",
        _gmail_import_err,
        sys.executable,
        _in_venv,
        _fix_cmd,
    )
    raise RuntimeError(
        f"[GMAIL] Required library google-auth is not installed in the active Python "
        f"environment ({sys.executable}).\n"
        f"Run: {_fix_cmd}"
    ) from _gmail_import_err

# ---------------------------------------------------------------------------
# Paths — use writable DATA_DIR for token persistence
# ---------------------------------------------------------------------------
try:
    from configs.settings import DATA_DIR as _DATA_DIR
    _CREDENTIALS_FILE = Path(str(_DATA_DIR)) / "credentials.json"
    _TOKEN_FILE = Path(str(_DATA_DIR)) / "gmail_token.json"
except Exception:
    _PROJECT_ROOT = Path(__file__).parent.parent
    _CREDENTIALS_FILE = _PROJECT_ROOT / "data" / "credentials.json"
    _TOKEN_FILE = _PROJECT_ROOT / "data" / "gmail_token.json"

# Gmail OAuth scopes — compose only (cannot read mail)
_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_gmail_service():
    """Authenticate and return an authorised Gmail API service object.

    Raises
    ------
    FileNotFoundError
        If ``data/credentials.json`` does not exist.
    ImportError
        If the required Google client libraries are not installed.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ImportError(
            "Gmail API libraries not installed. "
            "Run: pip install google-auth-oauthlib google-api-python-client"
        ) from exc

    if not _CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"Gmail credentials file not found: {_CREDENTIALS_FILE}\n"
            "Download your OAuth2 credentials JSON from Google Cloud Console and "
            f"save it as: {_CREDENTIALS_FILE}"
        )

    creds: Optional[Credentials] = None

    # Load persisted token if it exists
    if _TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
        except Exception as exc:
            log.warning("[GMAIL] Failed to load token file, re-authenticating: %s", exc)
            creds = None

    # Refresh or re-authorise as needed
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log.info("[GMAIL] Token refreshed successfully")
        except Exception as exc:
            log.warning("[GMAIL] Token refresh failed, re-authenticating: %s", exc)
            creds = None

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(_CREDENTIALS_FILE), _SCOPES
        )
        # Opens a browser window for one-time authorisation
        creds = flow.run_local_server(port=0)
        log.info("[GMAIL] OAuth flow completed, saving token")

    # Persist token for next run
    try:
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:
        log.warning("[GMAIL] Could not persist token: %s", exc)

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Draft builder
# ---------------------------------------------------------------------------

def _build_mime_message(
    to: str,
    subject: str,
    body: str,
    sender: Optional[str] = None,
    in_reply_to_msg_id: Optional[str] = None,
) -> MIMEMultipart:
    """Build an RFC-2822 MIME message suitable for the Gmail API."""
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    if sender:
        msg["From"] = sender
    if in_reply_to_msg_id:
        msg["In-Reply-To"] = in_reply_to_msg_id
        msg["References"] = in_reply_to_msg_id
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _encode_message(msg) -> str:
    """Encode a MIME message as base64url for the Gmail API."""
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# Public service class
# ---------------------------------------------------------------------------

class GmailService:
    """Thin wrapper around the Gmail API focused on draft creation."""

    def is_available(self) -> bool:
        """Return True if the Gmail credentials file exists and libraries are installed."""
        log.info("[GMAIL] is_available() check — credentials path: %s", _CREDENTIALS_FILE)
        try:
            import google.oauth2.credentials  # noqa: F401
            import googleapiclient.discovery  # noqa: F401
        except ImportError as exc:
            log.warning(
                "[GMAIL] is_available()=False — required library missing: %s\n"
                "  Fix: pip install google-auth-oauthlib google-api-python-client",
                exc,
            )
            return False
        creds_exists = _CREDENTIALS_FILE.exists()
        token_exists = _TOKEN_FILE.exists()
        log.info(
            "[GMAIL] is_available()=%s — credentials.json=%s, gmail_token.json=%s",
            creds_exists,
            _CREDENTIALS_FILE if creds_exists else f"MISSING ({_CREDENTIALS_FILE})",
            _TOKEN_FILE if token_exists else f"not yet created ({_TOKEN_FILE})",
        )
        return creds_exists

    def get_user_email(self) -> Optional[str]:
        """Return the authenticated user's email address, or None on failure."""
        try:
            service = _get_gmail_service()
            profile = service.users().getProfile(userId="me").execute()
            return profile.get("emailAddress")
        except Exception as exc:
            log.warning("[GMAIL] Could not retrieve user email: %s", exc)
            return None

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
        in_reply_to_msg_id: Optional[str] = None,
    ) -> dict:
        """Create a draft in Gmail Drafts folder.

        Parameters
        ----------
        to : str
            Recipient address (e.g. ``"alice@example.com"``).
        subject : str
            Email subject line.
        body : str
            Plain-text email body.
        thread_id : str, optional
            Gmail thread ID to associate the draft with an existing thread.
        in_reply_to_msg_id : str, optional
            Gmail message ID for In-Reply-To / References headers.

        Returns
        -------
        dict
            ``{"success": True, "draft_id": "<gmail_draft_id>",
               "gmail_draft_url": "<url>", "message": "..."}``
            on success, or
            ``{"success": False, "error": "<reason>"}``
            on failure.
        """
        log.info(
            "[GMAIL] create_draft() called — to=%s  subject=%s",
            to, subject,
        )

        if not self.is_available():
            msg = (
                f"Gmail API not configured. "
                f"credentials.json not found at: {_CREDENTIALS_FILE}"
            )
            log.error("[GMAIL] create_draft() aborted: %s", msg)
            return {"success": False, "error": msg}

        try:
            log.info("[GMAIL] Acquiring OAuth service (may open browser on first run)")
            service = _get_gmail_service()
            log.info("[GMAIL] OAuth service acquired — token stored at: %s", _TOKEN_FILE)

            mime_msg = _build_mime_message(
                to=to,
                subject=subject,
                body=body,
                in_reply_to_msg_id=in_reply_to_msg_id,
            )

            draft_body: dict = {"message": {"raw": _encode_message(mime_msg)}}
            if thread_id:
                draft_body["message"]["threadId"] = thread_id

            log.info("[GMAIL] Calling Gmail API: users.drafts.create (userId=me)")
            draft = service.users().drafts().create(
                userId="me", body=draft_body
            ).execute()

            log.info("[GMAIL] Raw API response: %s", draft)

            gmail_draft_id = draft.get("id", "")
            if not gmail_draft_id:
                log.error("[GMAIL] API returned no draft id — full response: %s", draft)
                return {"success": False, "error": "Gmail API returned empty draft id"}

            log.info("[GMAIL] Draft created in Gmail (id: %s)", gmail_draft_id)

            # Build a direct URL to the draft in Gmail web
            gmail_draft_url = (
                f"https://mail.google.com/mail/u/0/#drafts/{gmail_draft_id}"
            )
            log.info("[GMAIL] Draft URL: %s", gmail_draft_url)

            return {
                "success": True,
                "draft_id": gmail_draft_id,
                "gmail_draft_url": gmail_draft_url,
                "message": "Draft created in Gmail Drafts folder",
            }

        except FileNotFoundError as exc:
            log.error("[GMAIL] Credentials file missing: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
        except ImportError as exc:
            log.error("[GMAIL] Missing library: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log.error("[GMAIL] Unexpected error creating draft: %s", exc, exc_info=True)
            return {"success": False, "error": f"Gmail API error: {exc}"}


# ---------------------------------------------------------------------------
# Environment diagnostics
# ---------------------------------------------------------------------------

def verify_gmail_environment() -> dict:
    """Check and report the Gmail API environment status.

    Prints a human-readable status table and returns a dict with keys:
      python_executable, google_auth, google_auth_oauthlib,
      googleapiclient, credentials_json, gmail_token_json, ready

    Usage::

        from services.gmail_service import verify_gmail_environment
        verify_gmail_environment()
    """
    status: dict = {
        "python_executable": sys.executable,
        "google_auth": False,
        "google_auth_oauthlib": False,
        "googleapiclient": False,
        "credentials_json": _CREDENTIALS_FILE.exists(),
        "gmail_token_json": _TOKEN_FILE.exists(),
        "ready": False,
    }

    for lib, key in [
        ("google.auth", "google_auth"),
        ("google_auth_oauthlib", "google_auth_oauthlib"),
        ("googleapiclient", "googleapiclient"),
    ]:
        try:
            __import__(lib)
            status[key] = True
        except ImportError:
            status[key] = False

    status["ready"] = (
        status["google_auth"]
        and status["google_auth_oauthlib"]
        and status["googleapiclient"]
        and status["credentials_json"]
    )

    lines = [
        "\n===== Gmail Environment Status =====",
        f"  Python interpreter : {status['python_executable']}",
        f"  google-auth        : {'OK' if status['google_auth'] else 'MISSING  <- pip install google-auth'}",
        f"  google-auth-oauthlib: {'OK' if status['google_auth_oauthlib'] else 'MISSING  <- pip install google-auth-oauthlib'}",
        f"  googleapiclient    : {'OK' if status['googleapiclient'] else 'MISSING  <- pip install google-api-python-client'}",
        f"  credentials.json   : {'FOUND  ' + str(_CREDENTIALS_FILE) if status['credentials_json'] else 'MISSING <- place at: ' + str(_CREDENTIALS_FILE)}",
        f"  gmail_token.json   : {'FOUND  ' + str(_TOKEN_FILE) if status['gmail_token_json'] else 'not yet created (generated on first OAuth login)'}",
        f"  Overall ready      : {'YES - Gmail API can create drafts' if status['ready'] else 'NO  - see items marked MISSING above'}",
        "====================================\n",
    ]
    output = "\n".join(lines)
    print(output)
    log.info("%s", output)

    if not status["ready"]:
        missing_libs = [
            pkg
            for pkg, key in [
                ("google-auth", "google_auth"),
                ("google-auth-oauthlib", "google_auth_oauthlib"),
                ("google-api-python-client", "googleapiclient"),
            ]
            if not status[key]
        ]
        if missing_libs:
            fix_cmd = f'python -m pip install {" ".join(missing_libs)}'
            log.error(
                "[GMAIL] Missing libraries. Run with the SAME interpreter:\n  %s\n"
                "  (current interpreter: %s)",
                fix_cmd, sys.executable,
            )

    return status


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

gmail_service = GmailService()
