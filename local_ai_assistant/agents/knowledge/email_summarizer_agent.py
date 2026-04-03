import os
import json
from agents.knowledge.email_query_agent import improved_search_emails, load_all_emails

EMAIL_FILE = os.path.join("data", "emails.json")
CACHE_FILE = os.path.join("data", "email_cache.json")


def summarize_single_email(mail, max_chars=160):
    subject = mail.get("subject") or "(no subject)"
    body = (mail.get("body") or "").strip()
    # Produce a single-line professional summary
    one_line = body.replace("\n", " ").strip()
    if len(one_line) > max_chars:
        one_line = one_line[: max_chars - 3].rstrip() + "..."

    sender = mail.get("from") or "Unknown"
    return {
        "id": mail.get("id", "N/A"),
        "from": sender,
        "subject": subject,
        "summary": one_line,
    }


def summarize_emails_by_query(query, max_results=10):
    """Return a professional summary for emails matching `query`, newest first."""
    matches = improved_search_emails(query, max_results=max_results)
    if not matches:
        return f"No matching emails found for: {query}"

    # Sort by IMAP ID descending so most recent matching email is shown first
    try:
        matches = sorted(matches, key=lambda e: int(str(e.get("id", 0) or 0)), reverse=True)
    except Exception:
        pass

    lines = [f"Found {len(matches)} email(s) for: {query}", ""]
    for m in matches:
        s = summarize_single_email(m)
        date_str = m.get("date", "")
        date_part = f" | Date: {date_str}" if date_str else ""
        lines.append(f"- [{s['id']}] From: {s['from']} | Subject: {s['subject']}{date_part}")
        lines.append(f"  {s['summary']}")

    return "\n".join(lines)


# Backwards-compatible alias expected by tool_executor


def handle_email_summary(user_input: str = ""):
    """Summarize emails, optionally scoped to a specific context email.

    When called with a non-empty user_input (e.g. from tool_executor), the
    function first checks conversation memory for a ``last_email``.  If one
    exists it summarises that specific email.  Otherwise it searches by
    user_input query, falling back to a full inbox summary.

    Called with no arguments (legacy call sites) it works exactly as before:
    returns a full inbox summary, newest first.
    """
    if user_input and user_input.strip():
        # Try memory-first: summarise the email currently in context.
        try:
            from memory.conversation_memory import conversation_memory
            last_email = conversation_memory.get_last_email()
            if last_email:
                s = summarize_single_email(last_email)
                body = (last_email.get("body") or "").strip()
                lines = [
                    "Email Summary",
                    "=" * 40,
                    f"From     : {last_email.get('from', 'Unknown')}",
                    f"Subject  : {last_email.get('subject', '(no subject)')}",
                ]
                if last_email.get("date"):
                    lines.append(f"Date     : {last_email.get('date')}")
                lines.append("")
                lines.append(f"Summary  : {s['summary']}")
                if body:
                    lines.append("")
                    lines.append("Full Content:")
                    lines.append(body[:1000] + ("..." if len(body) > 1000 else ""))
                return "\n".join(lines)
        except Exception:
            pass
        # No email in memory — search by query
        return summarize_emails_by_query(user_input.strip())

    # No user_input — full inbox summary (original behaviour)
    emails = load_all_emails()
    if not emails:
        return "No emails available."

    # Sort by IMAP ID descending — highest ID = most recently received
    try:
        emails = sorted(emails, key=lambda e: int(str(e.get("id", 0) or 0)), reverse=True)
    except Exception:
        pass

    output = [f"Inbox — {len(emails)} emails (newest first)", ""]
    for mail in emails:
        s = summarize_single_email(mail)
        date_str = mail.get("date", "")
        output.append("--------------------")
        output.append(f"Email ID : {s['id']}")
        output.append(f"From     : {s['from']}")
        output.append(f"Subject  : {s['subject']}")
        if date_str:
            output.append(f"Date     : {date_str}")
        output.append(f"Summary  : {s['summary']}")
        output.append("--------------------")

    return "\n".join(output)


# Backwards-compatible alias expected by tool_executor
handle_email_summarizer = handle_email_summary  # type: ignore[assignment]