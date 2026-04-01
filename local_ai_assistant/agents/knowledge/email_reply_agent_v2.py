"""
agents/knowledge/email_reply_agent_v2.py
========================================
AI-assisted email reply generation with strict grounding and email selection.

Key Features:
- Email Selection: finds target email by latest, sender, index, or search results
- Hallucination Prevention: LLM is strictly constrained to only use actual email content
- Context Awareness: reuses previous email search results
- Robust Error Handling: graceful fallbacks when email not found

Usage::

    from agents.knowledge.email_reply_agent_v2 import (
        find_target_email,
        generate_email_reply,
    )

    # Find email and generate reply
    email = find_target_email(
        user_input="reply to alice",
        search_results=[]  # from previous search
    )
    if email:
        reply = generate_email_reply(email, tone="professional")
        print(reply)
    else:
        print("Email not found")
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from configs.settings import settings
from agents.knowledge.email_query_agent import load_all_emails, _format_email_results

log = logging.getLogger(__name__)


# ============================================================================
# EMAIL SELECTION LOGIC
# ============================================================================

def find_target_email(
    user_input: str,
    search_results: list[dict] | None = None,
) -> Optional[dict]:
    """
    Find the target email to reply to.

    Strategy (in order of precedence):
    1. Direct email ID reference in input ("id 12345", "#123")
    2. Sender email address in input ("reply to alice@example.com")
    3. Sender name in input ("reply to alice")
    4. Index reference ("first", "second", "latest") in context of search results
    5. Latest email overall

    Parameters
    ----------
    user_input : str
        The user query containing reply request (e.g., "reply to alice")
    search_results : list[dict], optional
        Email search results from previous query (used for indexed refs)

    Returns
    -------
    dict or None
        The target email dict, or None if not found
    """
    if not user_input:
        return None

    user_lower = user_input.lower()
    all_emails = load_all_emails()

    if not all_emails:
        log.warning("No emails found to reply to")
        return None

    # Strategy 1: Direct ID reference
    # Patterns: "id 12345", "#123", "email 12345"
    id_match = re.search(r"(?:id|#|email)\s*:?\s*(\d+)", user_lower)
    if id_match:
        email_id = id_match.group(1)
        for email in all_emails:
            if str(email.get("id", "")) == email_id:
                log.info("Found email by ID: %s", email_id)
                return email
        log.warning("Email ID %s not found", email_id)
        return None

    # Strategy 2: Email address pattern
    # Patterns: "reply to alice@example.com", "from alice@company.com"
    email_pattern_match = re.search(
        r"(?:to|from)\s+([\w\.\-]+@[\w\.\-]+\.\w+)",
        user_input,
        re.IGNORECASE
    )
    if email_pattern_match:
        target_email_addr = email_pattern_match.group(1).lower()
        matching = [e for e in all_emails
                   if target_email_addr in e.get("from", "").lower()]
        if matching:
            latest = max(matching, key=lambda e: int(str(e.get("id", 0) or 0)))
            log.info("Found email by address: %s", target_email_addr)
            return latest
        log.warning("No email from address: %s", target_email_addr)
        return None

    # Strategy 3: Sender name pattern
    # Patterns: "reply to alice", "respond to bob"
    name_match = re.search(
        r"(?:to|from)\s+([\w\-]+(?:\s+[\w\-]+)?)",
        user_input,
        re.IGNORECASE
    )
    if name_match:
        target_name = name_match.group(1).lower()
        matching = [e for e in all_emails
                   if target_name in e.get("from", "").lower()]
        if matching:
            latest = max(matching, key=lambda e: int(str(e.get("id", 0) or 0)))
            log.info("Found email by sender name: %s", target_name)
            return latest
        log.debug("No email from sender: %s", target_name)

    # Strategy 4: Index reference with search results
    # Patterns: "reply to first", "second email", "last email"
    if search_results and len(search_results) > 0:
        index_match = re.search(
            r"\b(first|second|third|1st|2nd|3rd|\d+(?:st|nd|rd|th)?|last|latest)\b",
            user_lower
        )
        if index_match:
            ref = index_match.group(1).lower()
            target_email = _get_email_by_index(ref, search_results)
            if target_email:
                log.info("Found email by index in search results: %s", ref)
                return target_email

    # Strategy 5: Fallback - latest email  
    if all_emails:
        latest = max(all_emails, key=lambda e: int(str(e.get("id", 0) or 0)))
        log.info("Using latest email as fallback")
        return latest

    return None


def _get_email_by_index(index_ref: str, emails: list[dict]) -> Optional[dict]:
    """Get email by index reference (first, second, 1st, 2nd, last, etc.)."""
    if not emails:
        return None

    lower = index_ref.lower()
    
    # Handle word references
    if lower in ("first", "1st"):
        return emails[0] if len(emails) > 0 else None
    if lower in ("second", "2nd"):
        return emails[1] if len(emails) > 1 else None
    if lower in ("third", "3rd"):
        return emails[2] if len(emails) > 2 else None
    if lower in ("last", "latest"):
        return emails[-1] if emails else None
    
    # Handle numeric references
    numeric_match = re.match(r"(\d+)", lower)
    if numeric_match:
        idx = int(numeric_match.group(1)) - 1  # Convert to 0-based
        if 0 <= idx < len(emails):
            return emails[idx]
    
    return None


# ============================================================================
# REPLY GENERATION
# ============================================================================

def generate_email_reply(
    email: dict,
    tone: str = "professional",
    context: Optional[str] = None,
    model_name: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a context-aware, role-correct email reply draft.

    The LLM is told it is replying AS the recipient (user_name), not the
    sender. It detects the email's intent (reminder, complaint, request, etc.)
    and produces a structured, human-sounding reply.

    Parameters
    ----------
    email : dict
        The email to reply to (must have 'from', 'subject', 'body')
    tone : str
        Tone of reply: "professional" (default), "friendly", "casual", "formal"
    context : str, optional
        Additional context (e.g., previous conversation summary)
    model_name : str, optional
        Model to use (default: from settings.model_name)
    user_name : str, optional
        Name of the person replying (default: settings.user_name)

    Returns
    -------
    str or None
        Generated reply text. Returns None if generation failed.
    """
    if not email:
        log.warning("No email provided to reply to")
        return None

    # Extract email components
    from_addr = email.get("from", "Unknown")
    subject = email.get("subject", "(No Subject)")
    body = email.get("body", "")

    # Validate email has content
    if not body or not body.strip():
        log.warning("Email body is empty, cannot generate meaningful reply")
        return None

    # Resolve the reply-author identity
    if user_name is None:
        user_name = settings.user_name  # e.g. "Sandeep" from USER_NAME env var

    # Build the context-aware prompt
    prompt = _build_strict_reply_prompt(from_addr, subject, body, tone, context, user_name)

    if model_name is None:
        model_name = settings.model_name

    try:
        import ollama

        log.debug("Generating reply (tone=%s, user_name=%s)", tone, user_name)

        response = ollama.generate(
            model=model_name,
            prompt=prompt,
            stream=False,
            options={
                "temperature": 0.6,
                "num_predict": 350,
                "top_k": 40,
                "top_p": 0.9,
            },
        )

        reply_text = response.get("response", "").strip()

        if reply_text:
            log.info("Generated reply (%d chars)", len(reply_text))
            return reply_text
        else:
            log.warning("LLM returned empty reply")
            return None

    except Exception as e:
        log.error("Failed to generate reply: %s", e, exc_info=True)
        return None


def _build_strict_reply_prompt(
    from_addr: str,
    subject: str,
    body: str,
    tone: str = "professional",
    context: Optional[str] = None,
    user_name: str = "Sandeep",
) -> str:
    """
    Build a context-aware, role-correct prompt for email reply generation.

    Key design principles:
    - LLM is told it IS the recipient (user_name) writing the reply.
    - LLM detects email intent (reminder/complaint/request/update) and responds
      appropriately rather than falling back to generic phrases.
    - Structured output enforced: Greeting → Acknowledgement → Action → Closing.
    - Signature is always user_name, never the sender's name.
    - "I don't have that information" is explicitly forbidden.
    """
    sender_name = _extract_name_from_email(from_addr)

    tone_desc = {
        "professional": "professional and courteous — concise, direct, formal language",
        "friendly": "friendly and warm — personable but still professional",
        "casual": "casual and brief — relaxed language, still respectful",
        "formal": "very formal — use formal salutations and full sentences",
    }.get(tone, "professional and courteous — concise, direct, formal language")

    context_block = f"\nAdditional context to consider:\n{context}\n" if context else ""

    prompt = f"""You are {user_name}. You received the following email and must write a reply.

RECEIVED EMAIL:
From: {from_addr}
Subject: {subject}
---
{body.strip()}
---
{context_block}
YOUR ROLE:
- You are {user_name}, the RECIPIENT of the email above.
- You are writing FROM {user_name} TO {sender_name}.
- Sign the email as {user_name}. Do NOT sign as {sender_name}.

STEP 1 — IDENTIFY THE EMAIL TYPE and respond accordingly:
- REMINDER or FOLLOW-UP → acknowledge the reminder, state when/how you will complete the task.
- COMPLAINT or CONCERN → apologise sincerely, take responsibility, state the corrective action.
- REQUEST or QUESTION → confirm you understood the request and state your answer or next action.
- UPDATE or FYI → acknowledge receipt, provide relevant feedback or next steps.

STEP 2 — WRITE THE REPLY using this exact structure:
1. Greeting   : Hi {sender_name},
2. Acknowledge : Acknowledge the specific point the sender raised (reference email details).
3. Action      : State clearly what you will do, have done, or what the resolution is.
4. Closing     : Best regards,\n{user_name}

RULES:
- Tone: {tone_desc}.
- Keep the reply under 150 words.
- Do NOT include email headers (To:, From:, Subject:) in the reply.
- Do NOT say "I don't have that information" — instead acknowledge and commit to finding out.
- Only use facts present in the email above; do not invent names, dates, or details.
- Sound like a real human, not a template.

DRAFT REPLY (begin directly with "Hi {sender_name},"):
"""

    return prompt


def _extract_name_from_email(from_header: str) -> str:
    """Extract friendly name from email From header."""
    if not from_header:
        return "there"

    # Try to extract name from format: "John Doe <john@example.com>"
    match = re.search(r"([^<]+)<", from_header)
    if match:
        name = match.group(1).strip()
        first_name = name.split()[0]
        return first_name

    # Try to extract name from simple format: "john@example.com"
    email_match = re.search(r"(\w+)@", from_header)
    if email_match:
        return email_match.group(1).capitalize()

    # Default
    return "there"


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_tone_options() -> dict[str, str]:
    """Return available tone options with descriptions."""
    return {
        "professional": "Professional and formal",
        "friendly": "Friendly and warm",
        "casual": "Casual and relaxed",
        "formal": "Very formal and detailed",
    }


# ============================================================================
# LEGACY COMPATIBILITY (wrapper for old API)
# ============================================================================

def generate_reply_to_latest_from_sender(
    sender_pattern: str,
    tone: str = "professional",
    context: Optional[str] = None
) -> Optional[tuple[dict, str]]:
    """
    Legacy function: Generate reply to latest email from specific sender.

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
        (original_email, reply_text) or None if not found
    """
    emails = load_all_emails()
    pattern_lower = sender_pattern.lower()

    matching_emails = [
        e for e in emails
        if pattern_lower in e.get("from", "").lower()
    ]

    if not matching_emails:
        log.warning("No emails found from: %s", sender_pattern)
        return None

    latest_email = max(matching_emails, key=lambda e: int(str(e.get("id", 0) or 0)))
    reply_text = generate_email_reply(latest_email, tone=tone, context=context)

    if reply_text:
        return (latest_email, reply_text)
    else:
        return None
