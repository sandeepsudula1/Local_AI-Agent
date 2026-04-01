"""
QUICK START - EMAIL REPLY FEATURE
==================================

Get the AI-assisted email reply feature working in 5 minutes.

## Step 1: Configure Email (SMTP)

Edit your .env file in the project root:

```ini
# For Gmail (recommended for testing)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASS=your-app-password        # Get this from https://myaccount.google.com/apppasswords
EMAIL_FROM=your-email@gmail.com     # Optional, defaults to EMAIL_USER
EMAIL_TLS=true

# OR for Outlook
EMAIL_HOST=smtp.office365.com
EMAIL_PORT=587
EMAIL_USER=your-email@outlook.com
EMAIL_PASS=your-password
EMAIL_TLS=true

# OR for another provider
EMAIL_HOST=mail.example.com
EMAIL_PORT=587
EMAIL_USER=your-username
EMAIL_PASS=your-password
EMAIL_TLS=true
```

ℹ️  Note: If you only use IMAP (sending only, not fetching), you need SMTP configured separately.

## Step 2: Verify Email Data Exists

Make sure you have email data:

```bash
# Check which file has emails:
ls -la data/emails.json data/email_cache.json

# Or test with Python:
python -c "from agents.knowledge.email_query_agent import load_all_emails; emails = load_all_emails(); print(f'Loaded {len(emails)} emails')"
```

If no emails, you need to:
1. Configure IMAP (EMAIL_HOST, EMAIL_USER, etc.) OR
2. Create sample data/emails.json:

```json
[
  {
    "id": "1",
    "from": "alice@example.com",
    "subject": "Project Update",
    "date": "Mon, 30 Mar 2026 10:00:00 +0000",
    "body": "Hi, can you update the status of the project? Thanks"
  },
  {
    "id": "2",
    "from": "bob@example.com",
    "subject": "Meeting Tomorrow",
    "date": "Mon, 30 Mar 2026 14:00:00 +0000",
    "body": "Let's meet tomorrow to discuss the strategy."
  }
]
```

## Step 3: Test the Feature

Use the interactive test:

```bash
python -c "
from agents.knowledge.email_reply_agent import generate_email_reply

# Generate a professional reply to email #1
reply = generate_email_reply('1', tone='professional')

if reply:
    print('✓ SUCCESS! Generated reply:')
    print('─' * 60)
    print(reply)
    print('─' * 60)
else:
    print('✗ Failed to generate reply')
    print('  Check:')
    print('  1. data/emails.json or data/email_cache.json exists')
    print('  2. Ollama is running (ollama serve)')
    print('  3. Model loaded (ollama pull llama3.2:1b)')
"
```

Or run the full test suite:

```bash
python scripts/test_email_reply.py
```

## Step 4: Use in Your Assistant

### Through the Orchestrator (Recommended)

```python
from core.intent_classifier import intent_classifier
from core.router import router
from core.tool_executor import tool_executor

# User asks for reply
user_input = "reply to alice's latest email in a friendly tone"

# Pipeline auto-routes it
intent = intent_classifier.classify(user_input)  # → EMAIL_REPLY
tool_name = router.route(intent)                   # → email.reply
result = tool_executor.execute(tool_name, user_input)

print(result.output)  # Draft reply shown to user
```

### Or Direct Calls

```python
from agents.knowledge.email_reply_agent import generate_email_reply
from services.email_send_service import send_email

# Step 1: Generate draft
reply = generate_email_reply(
    email_id="1",
    tone="professional"
)

# Step 2: Send after user confirms
if reply:
    success, msg = send_email(
        to="alice@example.com",
        subject="Re: Project Update",
        body=reply,
        confirm=True
    )
    print(msg)
```

## Step 5: User Conversations

### Example 1: Professional Reply

```
User: "Reply to the latest email in a professional tone"

System: 
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📧 DRAFT REPLY (Professional Tone)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Dear Alice,

Thank you for your email. I understand you need an update on the project
status. I am currently working on the deliverables and expect to have a
comprehensive update for you by the end of the week.

Please feel free to reach out if you need clarification on anything.

Best regards,
[Your Name]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Reply draft generated (From: alice@example.com)
💡 You can: review it, edit it, or say "send the reply"
⚠️  Email will NOT be sent until you confirm

User: "Send the reply"

System:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📧 EMAIL CONFIRMATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
To: alice@example.com
Subject: Re: Project Update

Dear Alice,

Thank you for your email...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Email sent successfully!
  From: your-email@gmail.com
  To: alice@example.com
```

### Example 2: Friendly Reply

```
User: "Draft a friendly reply to bob about the meeting"

System: [Generates friendly tone reply]

📧 DRAFT REPLY (Friendly Tone)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Hey Bob,

Thanks so much for reaching out! I'd be happy to meet tomorrow and chat
about the strategy. What time works best for you?

Looking forward to it!

━━━━━━━━━━━━━━━━━━

User: "Yes, send it"
System: ✓ Email sent!
```

## Tone Selection

Available tones (picked automatically from your query):

| Tone | When to Use | Example |
|------|-------------|---------|
| **professional** (default) | Formal business | "reply professionally" |
| **friendly** | Warm colleague | "reply in a friendly tone" |
| **casual** | Relaxed coworker | "reply casually" |
| **formal** | Very formal | "reply very formally" |

## Troubleshooting

### Problem: "No email found to reply to"

```python
# Solution: Load emails first
from agents.knowledge.email_query_agent import load_all_emails

emails = load_all_emails()
print(f"Emails available: {len(emails)}")

if len(emails) == 0:
    print("✗ Add emails to data/emails.json or configure IMAP")
```

### Problem: "Failed to generate reply"

```bash
# Check 1: Ollama running?
ollama serve

# Check 2: Model available?
ollama list

# Check 3: Model loaded?
ollama pull llama3.2:1b

# Check 4: Test directly?
python -c "import ollama; print(ollama.list())"
```

### Problem: "SMTP authentication failed"

```python
# Solution: Verify credentials
import os
print(f"Host: {os.getenv('EMAIL_HOST')}")
print(f"Port: {os.getenv('EMAIL_PORT')}")
print(f"User: {os.getenv('EMAIL_USER')}")
print(f"Password length: {len(os.getenv('EMAIL_PASS', ''))}")

# For Gmail: https://myaccount.google.com/apppasswords
# NOT your regular Gmail password
```

### Problem: "Connection refused"

```bash
# Test SMTP connection:
telnet smtp.gmail.com 587

# Should show: 220 smtp.gmail.com ESMTP ...

# If fails: check firewall, check host/port
```

## Testing Checklist

- [ ] Email data loaded (data/emails.json or email_cache.json)
- [ ] SMTP credentials configured in .env
- [ ] Ollama running (`ollama serve`)
- [ ] Model available (`ollama list` shows llama3.2:1b)
- [ ] Intent detection works ("reply to..." → EMAIL_REPLY)
- [ ] Reply generation works (generate_email_reply returns text)
- [ ] Email sending works (send_email succeeds)
- [ ] No auto-sending (requires confirm=True)
- [ ] Tone selection works (professional/friendly/casual/formal)

## Working Examples (Copy & Paste)

### Test 1: Generate Reply

```python
from agents.knowledge.email_reply_agent import generate_email_reply

reply = generate_email_reply("1", tone="professional")
print(f"Generated {len(reply)} char reply:")
print(reply)
```

### Test 2: Intent Detection

```python
from core.intent_classifier import intent_classifier

intent = intent_classifier.classify("reply to alice's email")
print(f"Intent: {intent}")  # Should be EMAIL_REPLY
```

### Test 3: Send Email

```python
from services.email_send_service import send_email

success, msg = send_email(
    to="alice@example.com",
    subject="Test Email",
    body="This is a test email.",
    confirm=True
)
print(msg)
```

### Test 4: Full Pipeline

```python
from core.intent_classifier import intent_classifier
from core.router import router
from core.tool_executor import tool_executor

user_input = "draft a reply to alice in a friendly tone"
intent = intent_classifier.classify(user_input)
tool_name = router.route(intent)
result = tool_executor.execute(tool_name, user_input)

print(f"Intent: {intent}")
print(f"Tool: {tool_name}")
print(f"Success: {result.success}")
print(f"Output:\\n{result.output}")
```

## Common Questions

**Q: Will emails auto-send?**
A: Never. User must explicitly confirm "yes, send it" or similar.

**Q: Can I edit the draft?**
A: Currently the draft is shown for review. Edit functionality would be a future enhancement.

**Q: What LLM is used?**
A: The default LLM from settings.model_name (usually llama3.2:1b). Customizable.

**Q: Does it work with any email provider?**
A: Yes, any SMTP provider. Gmail, Outlook, custom servers all supported.

**Q: Is my password safe?**
A: Loaded from .env (never hardcoded, never logged). Use app-specific passwords for Gmail.

**Q: What if my email is long?**
A: Body is truncated to ~1000 chars for LLM efficiency. Full email still sent in reply.

## Next Steps

1. ✓ Configure .env (5 min)
2. ✓ Test generation (1 min)
3. ✓ Test sending (2 min)
4. ✓ Integrate into assistant (depends on your flow)

Done! 🎉

For detailed docs, see: EMAIL_REPLY_IMPLEMENTATION_GUIDE.md
"""
