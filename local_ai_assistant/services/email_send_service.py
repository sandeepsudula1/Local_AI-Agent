"""
services/email_send_service.py
=============================
SMTP-based email sending service (requires user confirmation before sending).

Supports multiple email providers with automatic configuration detection.
All sends require explicit user confirmation - NEVER auto-sends.

Usage::

    from services.email_send_service import send_email_confirmation

    # Generate confirmation message
    confirm_msg = send_email_confirmation(
        to="alice@company.com",
        subject="Re: Project Update",
        body="Thank you for the update..."
    )
    print(confirm_msg)

    # After user confirms:
    success, message = send_email(
        to="alice@company.com",
        subject="Re: Project Update",
        body="Thank you for the update...",
        confirm=True
    )
"""

from __future__ import annotations

import os
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class SMTPConfig:
    """SMTP configuration for an email provider."""
    host: str
    port: int
    use_tls: bool = True
    user: Optional[str] = None
    password: Optional[str] = None
    from_email: Optional[str] = None


def get_smtp_config() -> Optional[SMTPConfig]:
    """
    Get SMTP configuration from environment variables.

    Environment variables:
    - EMAIL_HOST: SMTP server hostname
    - EMAIL_PORT: SMTP server port
    - EMAIL_USER: SMTP login username
    - EMAIL_PASS: SMTP login password
    - EMAIL_FROM: Sender email address (optional, defaults to EMAIL_USER)
    - EMAIL_TLS: Use TLS (default: True)

    Returns
    -------
    SMTPConfig or None
        SMTP configuration, or None if required settings missing.
    """
    host = os.getenv("EMAIL_HOST")
    port_str = os.getenv("EMAIL_PORT")
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not (host and port_str):
        log.warning("SMTP not configured (missing EMAIL_HOST or EMAIL_PORT)")
        return None

    try:
        port = int(port_str)
    except ValueError:
        log.error("Invalid EMAIL_PORT: %s", port_str)
        return None

    from_email = os.getenv("EMAIL_FROM") or user
    use_tls = os.getenv("EMAIL_TLS", "true").lower() in ("true", "1", "yes")

    return SMTPConfig(
        host=host,
        port=port,
        use_tls=use_tls,
        user=user,
        password=password,
        from_email=from_email,
    )


def send_email_confirmation(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> str:
    """
    Generate a confirmation message BEFORE sending.

    Shows user what will be sent so they can approve or cancel.

    Parameters
    ----------
    to : str
        Recipient email address
    subject : str
        Email subject
    body : str
        Email body
    cc : str, optional
        CC recipient(s)
    bcc : str, optional
        BCC recipient(s)

    Returns
    -------
    str
        Formatted confirmation message for user review

    Example
    -------
    >>> msg = send_email_confirmation(
    ...     to="alice@company.com",
    ...     subject="Re: Meeting",
    ...     body="Thanks for the meeting..."
    ... )
    >>> print(msg)
    >>> # User reads and confirms: "yes, send it"
    """
    lines = [
        "📧 EMAIL CONFIRMATION",
        "=" * 50,
        f"To: {to}",
    ]

    if cc:
        lines.append(f"CC: {cc}")
    if bcc:
        lines.append(f"BCC: {bcc}")

    lines.extend([
        f"Subject: {subject}",
        "-" * 50,
        body,
        "-" * 50,
        "",
        "⚠️  Please review carefully before confirming.",
        "Reply with: 'yes, send' to send, or 'no, cancel' to abort.",
    ])

    return "\n".join(lines)


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    confirm: bool = True,
    smtp_config: Optional[SMTPConfig] = None,
) -> Tuple[bool, str]:
    """
    Send an email via SMTP (requires confirmation).

    Parameters
    ----------
    to : str
        Recipient email address
    subject : str
        Email subject
    body : str
        Email body
    cc : str, optional
        CC recipient(s), comma-separated
    bcc : str, optional
        BCC recipient(s), comma-separated
    confirm : bool
        SAFETY: Must be True to send (prevents accidental sends)
    smtp_config : SMTPConfig, optional
        Custom SMTP config (default: load from environment)

    Returns
    -------
    tuple[bool, str]
        (success: bool, message: str)
        - (True, "Email sent to alice@company.com")
        - (False, "SMTP not configured")
        - (False, "Confirmation required (confirm=True)")

    Example
    -------
    >>> # Step 1: Show confirmation message
    >>> confirm_msg = send_email_confirmation(
    ...     to="alice@company.com",
    ...     subject="Re: Project",
    ...     body="Thanks for the update..."
    ... )
    >>> print(confirm_msg)

    >>> # Step 2: User reviews and confirms
    >>> # Step 3: User says "yes, send"
    >>> success, msg = send_email(
    ...     to="alice@company.com",
    ...     subject="Re: Project",
    ...     body="Thanks for the update...",
    ...     confirm=True  # ✓ Safety check
    ... )
    >>> print(msg)
    """

    # Safety check: prevent accidental sends
    if not confirm:
        log.warning("Email send blocked: confirmation required")
        return (
            False,
            "⚠️ Email send requires explicit confirmation (confirm=True). "
            "Please review and confirm the email content first.",
        )

    # Get SMTP config
    if smtp_config is None:
        smtp_config = get_smtp_config()

    if not smtp_config:
        log.error("SMTP not configured")
        return (
            False,
            "❌ Email sending not configured. "
            "Please set EMAIL_HOST, EMAIL_PORT, EMAIL_USER, and EMAIL_PASS in .env",
        )

    if not smtp_config.user or not smtp_config.password:
        log.error("SMTP credentials missing")
        return (
            False,
            "❌ SMTP credentials not configured. "
            "Please set EMAIL_USER and EMAIL_PASS in .env",
        )

    # Validate recipient
    if not _is_valid_email(to):
        log.error("Invalid recipient email: %s", to)
        return (False, f"❌ Invalid recipient email: {to}")

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        # Build email message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_config.from_email
        msg["To"] = to

        if cc:
            msg["CC"] = cc
        if bcc:
            msg["BCC"] = bcc

        # Attach body (plain text)
        msg.attach(MIMEText(body, "plain"))

        # Prepare recipient list
        recipients = [to]
        if cc:
            recipients.extend([x.strip() for x in cc.split(",")])
        if bcc:
            recipients.extend([x.strip() for x in bcc.split(",")])

        log.debug(
            "Sending email via %s:%d to %s",
            smtp_config.host,
            smtp_config.port,
            to,
        )

        # Connect and send
        with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
            if smtp_config.use_tls:
                server.starttls()

            server.login(smtp_config.user, smtp_config.password)
            server.sendmail(smtp_config.from_email, recipients, msg.as_string())

        log.info("Email sent successfully to %s", to)
        return (
            True,
            f"✓ Email sent successfully to {to}\n"
            f"Subject: {subject}",
        )

    except smtplib.SMTPAuthenticationError:
        log.error("SMTP authentication failed")
        return (
            False,
            "❌ SMTP authentication failed. "
            "Please check EMAIL_USER and EMAIL_PASS in .env",
        )
    except smtplib.SMTPException as e:
        log.error("SMTP error: %s", e)
        return (False, f"❌ Email send failed: {e}")
    except Exception as e:
        log.error("Unexpected error sending email: %s", e, exc_info=True)
        return (False, f"❌ Unexpected error: {e}")


def _is_valid_email(email_address: str) -> bool:
    """Simple email validation."""
    import re

    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email_address))


def get_email_from_config() -> Optional[str]:
    """Get the 'from' email address from config."""
    config = get_smtp_config()
    return config.from_email if config else None
