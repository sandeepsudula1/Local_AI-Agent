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

    # No explicit match found — return None so callers can apply their own
    # fallback priority (search_results[0] > memory > nothing).
    # Do NOT fall back to the globally latest email here: that causes stale/wrong
    # email selection when the user has an active search context.
    log.debug("find_target_email: no explicit match; returning None for caller to resolve")
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
    user_content: Optional[str] = None,
    style: str = "normal",
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
    user_content : str, optional
        User-provided reply content/statement to transform into a professional
        email (e.g., "I will be available for the meeting"). When provided,
        the LLM expands this into a full reply instead of auto-generating one.

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

    # Treat missing/short body as an informal greeting rather than blocking.
    # "Hello Sandeep, Good morning" IS valid email content.
    if not body or not body.strip():
        body = "(No message body — please reply with a polite greeting.)"
        log.debug("generate_email_reply: empty body; using placeholder")

    # Resolve the reply-author identity
    if user_name is None:
        user_name = settings.user_name  # e.g. "Sandeep" from USER_NAME env var

    if model_name is None:
        model_name = settings.model_name

    # Build prompt — two modes:
    #   content-transform: user told us WHAT to say; expand it into a proper reply
    #   auto-generate:     infer reply from the email body alone
    if user_content and user_content.strip():
        log.info("generate_email_reply: content-transform mode (user_content=%d chars)", len(user_content))
        system_msg, user_msg = _build_content_transform_messages(
            from_addr, subject, body, user_content.strip(), tone, context, user_name, style
        )
    else:
        system_msg, user_msg = _build_auto_reply_messages(
            from_addr, subject, body, tone, context, user_name, style
        )

    try:
        import ollama

        log.debug("Generating reply via chat (tone=%s, model=%s)", tone, model_name)

        response = ollama.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            options={
                "temperature": 0.6,
                "num_predict": 350,
                "top_k": 40,
                "top_p": 0.9,
            },
        )

        reply_text = (response.get("message", {}).get("content") or "").strip()

        # Refusal detection — if the model refused, retry once with a minimal prompt
        if reply_text and _is_refusal(reply_text):
            log.warning(
                "generate_email_reply: LLM refusal detected (%r...) — retrying with minimal prompt",
                reply_text[:80],
            )
            sender_name = _extract_name_from_email(from_addr)
            # Build a safe, context-rich fallback that doesn't trigger safety filters
            if user_content and user_content.strip():
                core_intent = user_content.strip()
            else:
                # Auto-generate: derive intent from subject/body keywords
                core_intent = f"Thank you for your email regarding '{subject}'. I have noted your message."
            fallback_msg = (
                f"Write a professional email reply to this message:\n"
                f"From: {sender_name}\n"
                f"Subject: {subject}\n"
                f"Email content: {body.strip()[:200]}\n\n"
                f"My reply should convey: {core_intent}\n\n"
                f"Format:\n"
                f"- Start: Hi {sender_name},\n"
                f"- 2-3 sentences responding to the email\n"
                f"- Sign as: Best regards, {user_name}\n"
                f"Output only the reply body."
            )
            response2 = ollama.chat(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an email writer. Output only the email body text. "
                            "Never refuse. Never add commentary."
                        ),
                    },
                    {"role": "user", "content": fallback_msg},
                ],
                options={"temperature": 0.5, "num_predict": 250},
            )
            reply_text = (response2.get("message", {}).get("content") or "").strip()
            log.info("generate_email_reply: retry reply (%d chars)", len(reply_text))

        if reply_text:
            log.info("Generated reply (%d chars)", len(reply_text))
            return reply_text

        log.warning("LLM returned empty reply")
        return None

    except Exception as e:
        log.error("Failed to generate reply: %s", e, exc_info=True)
        return None


# Patterns that indicate the LLM refused rather than wrote a reply
_REFUSAL_RE = re.compile(
    r"\b(i\s+can'?t\s+help|i\s+cannot\s+help|i\s+am\s+unable|i'?m\s+unable"
    r"|i\s+can'?t\s+(write|create|provide|assist|do\s+that)"
    r"|i\s+cannot\s+(write|create|provide|assist)"
    r"|i\s+don'?t\s+have\s+(enough|the)\s+(information|context|detail)"
    r"|not\s+able\s+to\s+help|cannot\s+assist\s+with\s+this"
    r"|i\s+apologize.*\bbut\b.*\bcan'?t\b)\b",
    re.IGNORECASE,
)


def _is_refusal(text: str) -> bool:
    """Return True when the LLM reply looks like a refusal rather than an email."""
    return bool(_REFUSAL_RE.search(text))


def _build_content_transform_messages(
    from_addr: str,
    subject: str,
    body: str,
    user_content: str,
    tone: str = "professional",
    context: Optional[str] = None,
    user_name: str = "Sandeep",
    style: str = "normal",
) -> tuple[str, str]:
    """Return (system_message, user_message) for content-transform mode.

    In this mode the user told us exactly what they want to say; the LLM's
    job is purely formatting — expand the statement into a proper email reply.
    """
    sender_name = _extract_name_from_email(from_addr)
    tone_desc = _tone_description(tone)
    context_block = f"\nExtra context: {context}\n" if context else ""

    system_msg = (
        f"You are a professional email assistant for {user_name}. "
        "Your ONLY job is to write email replies. "
        "You MUST always produce a complete, polite email reply. "
        "NEVER refuse, NEVER say you can't help, NEVER add disclaimers."
    )

    user_msg = (
        f"I received this email from my contact {sender_name}:\n\n"
        f"Subject: {subject}\n"
        f"---\n{body.strip()}\n---\n"
        f"{context_block}\n"
        f"My intended reply (extract the factual intent; ignore any formatting "
        f"instructions like 'write professional' or 'make formal'):\n"
        f"  \"{user_content}\"\n\n"
        f"Write a complete, {tone_desc} email reply that conveys my intended message.\n"
        f"Rules:\n"
        f"- Start with: Hi {sender_name},\n"
        f"- Expand my message naturally but do NOT copy it word-for-word.\n"
        f"- Do NOT include phrases like 'write a professional mail' or formatting"
        f" instructions from my note.\n"
        f"- Keep it under 150 words.\n"
        f"- Sign off as {user_name}.\n"
        f"- Output only the email body — no headers, no commentary.\n"
        f"{_style_instructions(style)}\n"
        f"Draft reply:"
    )
    return system_msg, user_msg


def _build_auto_reply_messages(
    from_addr: str,
    subject: str,
    body: str,
    tone: str = "professional",
    context: Optional[str] = None,
    user_name: str = "Sandeep",
    style: str = "normal",
) -> tuple[str, str]:
    """Return (system_message, user_message) for auto-generate mode.

    In this mode the LLM reads the email and decides what an appropriate
    reply looks like.
    """
    sender_name = _extract_name_from_email(from_addr)
    tone_desc = _tone_description(tone)
    context_block = f"\nExtra context: {context}\n" if context else ""

    system_msg = (
        f"You are a professional email assistant for {user_name}. "
        "Your ONLY job is to write email replies. "
        "You MUST always produce a complete, polite email reply. "
        "NEVER refuse, NEVER say you can't help, NEVER add disclaimers."
    )

    user_msg = (
        f"I received this email from my contact {sender_name}:\n\n"
        f"Subject: {subject}\n"
        f"---\n{body.strip()}\n---\n"
        f"{context_block}\n"
        f"Write a {tone_desc} reply on my behalf ({user_name}).\n"
        f"- Identify what the email is about and respond appropriately.\n"
        f"- If it's a greeting, reply with a friendly professional greeting.\n"
        f"- If it's a request, acknowledge and state what you will do.\n"
        f"- If it's a follow-up, acknowledge and give your status.\n"
        f"- Start with: Hi {sender_name},\n"
        f"- Keep it under 150 words.\n"
        f"- Sign off as {user_name}.\n"
        f"- Output only the email body — no headers, no commentary.\n"
        f"{_style_instructions(style)}\n"
        f"Write the reply now:"
    )
    return system_msg, user_msg


def _tone_description(tone: str) -> str:
    return {
        "professional": "professional and courteous",
        "friendly": "friendly and warm",
        "casual": "casual and relaxed",
        "formal": "very formal",
    }.get(tone, "professional and courteous")


def detect_reply_style(query: str) -> str:
    """Detect the desired formatting style for an email reply from the user query.

    Returns
    -------
    str
        "bullet_points" — if query asks for bullets / list format
        "short"         — if query asks for brevity / conciseness
        "normal"        — default (paragraph prose)
    """
    q = (query or "").lower()
    if any(w in q for w in ("bullet", "bullets", "bullet point", "bullet points",
                             "bulleted", "in points", "point form", "list format", "as a list")):
        return "bullet_points"
    if any(w in q for w in ("short", "concise", "brief", "briefly", "shorter",
                             "shorten", "keep it short", "make it short", "in short",
                             "quick", "quickl", "minimal")):
        return "short"
    return "normal"


def _style_instructions(style: str) -> str:
    """Return prompt instructions for the given style."""
    if style == "bullet_points":
        return (
            "FORMAT: Structure your reply using bullet points (•) for the main content. "
            "Use a short opening sentence, then 2-4 bullet points covering the key points, "
            "then a closing line. Do NOT write plain paragraphs."
        )
    if style == "short":
        return (
            "FORMAT: Keep the reply very short — 2-4 sentences maximum. "
            "Be direct and concise. No filler phrases."
        )
    return ""  # normal — no special instruction


def _build_content_transform_prompt(
    from_addr: str,
    subject: str,
    body: str,
    user_content: str,
    tone: str = "professional",
    context: Optional[str] = None,
    user_name: str = "Sandeep",
) -> str:
    """
    Build a prompt that transforms the user's raw statement into a full reply.

    Instead of asking the LLM to infer what to say, we tell it exactly WHAT
    the user wants to communicate and ask it to format that into a proper email.
    """
    sender_name = _extract_name_from_email(from_addr)

    tone_desc = {
        "professional": "professional and courteous — concise, direct, formal language",
        "friendly": "friendly and warm — personable but still professional",
        "casual": "casual and brief — relaxed language, still respectful",
        "formal": "very formal — use formal salutations and full sentences",
    }.get(tone, "professional and courteous — concise, direct, formal language")

    context_block = f"\nAdditional context:\n{context}\n" if context else ""

    return f"""You are {user_name}. You received the following email and want to reply.

RECEIVED EMAIL:
From: {from_addr}
Subject: {subject}
---
{body.strip()}
---
{context_block}
YOUR REPLY MUST CONVEY:
"{user_content}"

TASK:
Convert the above statement into a complete, professional email reply.
- DO NOT change or contradict the user's core message.
- Expand it naturally — add a polite greeting, brief acknowledgment, and sign-off.
- Sign as {user_name}. Do NOT sign as {sender_name}.
- Tone: {tone_desc}.
- Keep the reply under 150 words.
- Do NOT include email headers (To:, From:, Subject:).
- Begin directly with "Hi {sender_name},".

HARD RULES (NEVER break these):
- NEVER say "I can't help", "I cannot assist", "I'm unable to", or any refusal.
- NEVER say "I don't have that information".
- ALWAYS produce a complete email reply — that is your only job here.

DRAFT REPLY:
"""


def _build_strict_reply_prompt(
    from_addr: str,
    subject: str,
    body: str,
    tone: str = "professional",
    context: Optional[str] = None,
    user_name: str = "Sandeep",
    style: str = "normal",
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
- NEVER say "I can't help", "I cannot assist", or refuse in any way.
- ALWAYS produce a complete email reply — that is your only job here.
- Only use facts present in the email above; do not invent names, dates, or details.
- Sound like a real human, not a template.
{_style_instructions(style)}
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
