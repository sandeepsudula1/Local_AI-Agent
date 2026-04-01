"""
agents/knowledge/email_reply_agent.py
====================================
AI-assisted email reply generation.

Generates draft email replies based on the original email content,
with support for tone selection and context awareness.

Usage::

    from agents.knowledge.email_reply_agent import generate_email_reply

    # Generate reply to the latest email from a sender
    reply = generate_email_reply(
        email_id="12345",
        tone="professional",
        context=None
    )
    print(reply)
    # → "Dear Alice,\n\nThank you for your email..."
"""

from __future__ import annotations

import logging
from typing import Optional

from configs.settings import settings
from agents.knowledge.email_query_agent import load_all_emails, _format_email_results

log = logging.getLogger(__name__)


def generate_email_reply(
    email_id: str,
    tone: str = "professional",
    context: Optional[str] = None,
    model_name: Optional[str] = None
) -> Optional[str]:
    """
    Generate a professional email reply draft.

    Parameters
    ----------
    email_id : str
        ID of the email to reply to.
    tone : str
        Tone of reply: "professional" (default), "friendly", "casual", "formal"
    context : str, optional
        Additional context to consider (e.g., previous conversation summary)
    model_name : str, optional
        Model to use (default: from settings.model_name)

    Returns
    -------
    str or None
        Generated reply text. Returns None if email not found or generation failed.

    Example
    -------
    >>> reply = generate_email_reply(
    ...     email_id="12345",
    ...     tone="professional"
    ... )
    >>> if reply:
    ...     print("Draft reply:")
    ...     print(reply)
    ... else:
    ...     print("Failed to generate reply")
    """
    # Fetch the email to reply to
    email = _fetch_email_by_id(email_id)
    if not email:
        log.warning("Email %s not found", email_id)
        return None

    # Build the prompt
    prompt = _build_reply_prompt(email, tone, context)

    # Call LLM
    if model_name is None:
        model_name = settings.model_name

    try:
        import ollama

        log.debug("Generating reply for email %s (tone=%s)", email_id, tone)

        response = ollama.generate(
            model=model_name,
            prompt=prompt,
            stream=False,
            options={
                "temperature": 0.7,
                "num_predict": 300,
            },
        )

        reply_text = response.get("response", "").strip()

        if reply_text:
            log.info("Generated reply for email %s (%d chars)", email_id, len(reply_text))
            return reply_text
        else:
            log.warning("LLM returned empty reply")
            return None

    except Exception as e:
        log.error("Failed to generate reply: %s", e, exc_info=True)
        return None


def generate_reply_to_latest_from_sender(
    sender_pattern: str,
    tone: str = "professional",
    context: Optional[str] = None
) -> Optional[tuple[dict, str]]:
    """
    Generate reply to the latest email from a specific sender.

    Parameters
    ----------
    sender_pattern : str
        Sender name or email address pattern (case-insensitive substring match)
    tone : str
        Tone of reply: "professional", "friendly", "casual", "formal"
    context : str, optional
        Additional context

    Returns
    -------
    tuple[dict, str] or None
        (original_email, reply_text) or None if no email found or generation failed

    Example
    -------
    >>> result = generate_reply_to_latest_from_sender("alice@company.com")
    >>> if result:
    ...     email, reply = result
    ...     print(f"replying to: {email['subject']}")
    ...     print(f"reply: {reply}")
    """
    # Find latest email from sender
    emails = load_all_emails()
    pattern_lower = sender_pattern.lower()

    matching_emails = [
        e for e in emails
        if pattern_lower in (e.get("from", "").lower() or "")
    ]

    if not matching_emails:
        log.warning("No emails found from sender matching: %s", sender_pattern)
        return None

    # Get latest (highest ID)
    latest_email = max(matching_emails, key=lambda e: int(str(e.get("id", 0) or 0)))
    email_id = str(latest_email.get("id", ""))

    # Generate reply
    reply_text = generate_email_reply(email_id, tone=tone, context=context)

    if reply_text:
        return (latest_email, reply_text)
    else:
        return None


def _fetch_email_by_id(email_id: str) -> Optional[dict]:
    """Fetch email data by ID from cache."""
    emails = load_all_emails()
    target_id = str(email_id)

    for email in emails:
        if str(email.get("id", "")) == target_id:
            return email

    return None


def _build_reply_prompt(
    email: dict,
    tone: str = "professional",
    context: Optional[str] = None
) -> str:
    """Build the prompt for LLM to generate a reply."""

    # Tone instructions
    tone_instructions = {
        "professional": (
            "Write a professional, courteous response. "
            "Be concise and direct. Use formal language."
        ),
        "friendly": (
            "Write a friendly and warm response. "
            "Be personable but still professional. Use a conversational tone."
        ),
        "casual": (
            "Write a casual, brief response. "
            "You can be more relaxed in language but stay professional."
        ),
        "formal": (
            "Write a very formal response. "
            "Use formal salutations and closing. Be detailed and respectful."
        ),
    }

    tone_desc = tone_instructions.get(tone, tone_instructions["professional"])

    # Extract email components
    _sender_name = _extract_sender_name(email.get("from", ""))
    subject = email.get("subject", "")
    body = email.get("body", "")

    # Build prompt
    prompt = f"""You are an AI assistant helping draft professional email replies.

ORIGINAL EMAIL:
From: {email.get('from', 'Unknown')}
Date: {email.get('date', 'Unknown')}
Subject: {subject}
---
{body}
---

INSTRUCTIONS:
1. {tone_desc}
2. Do NOT make assumptions or add information not in the original email.
3. Keep the reply concise (under 200 words).
4. Address the sender by name if possible.
5. Start with an appropriate greeting and end with a professional closing.
6. Reply directly to the points raised in the original email.
7. Do NOT include "Subject:", "From:", or any email headers in your reply.
8. Do NOT make up details or information not mentioned in the original email.
{f'9. Context: {context}' if context else ''}

DRAFT REPLY:
"""

    return prompt


def _extract_sender_name(from_header: str) -> str:
    """Extract friendly name from email From header."""
    if not from_header:
        return "there"

    # Try to extract name from format: "John Doe <john@example.com>"
    import re

    match = re.match(r'^([^<]+)<', from_header)
    if match:
        name = match.group(1).strip()
        if name and name != "":
            return name

    # Try email prefix
    match = re.match(r'^([^@]+)@', from_header)
    if match:
        email_prefix = match.group(1)
        # Convert email_prefix to title case
        return email_prefix.replace(".", " ").title()

    # Fallback
    return "there"


def get_tone_options() -> dict[str, str]:
    """Return available tone options with descriptions."""
    return {
        "professional": "Formal, courteous, direct (default)",
        "friendly": "Personable, warm, conversational",
        "casual": "Relaxed, brief, friendly",
        "formal": "Very formal, detailed, respectful",
    }
