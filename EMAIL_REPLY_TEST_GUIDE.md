# EMAIL_REPLY Feature - Quick Test Guide

## Testing the Fixes

### Prerequisites
- Ollama running with your configured model
- IMAP credentials configured
- Test emails in your mailbox

---

## Test 1: Intent Classification Fix
**What was broken**: "reply to email from alice" → EMAIL_SEARCH (wrong!)  
**What's fixed**: Now correctly → EMAIL_REPLY

### Test Steps
```
1. Send to assistant: "reply to email from alice"

Expected:
✓ Intent classified as EMAIL_REPLY
✓ Tool executor calls email.reply handler
✓ NOT classified as EMAIL_SEARCH

Check logs for:
  "Handling EMAIL_REPLY:" in tool_executor output
```

---

## Test 2: Email Selection Logic
**What was broken**: Could only find latest email or by sender name  
**What's fixed**: Now supports 5 strategies including index-based selection

### Test Steps

#### A. Latest Email (Fallback)
```
1. Say: "reply to latest email"

Expected:
✓ Finds most recent email by ID
✓ Generates reply
```

#### B. By Sender Name  
```
1. Say: "reply to alice"
2. (Or: "reply to bob@company.com")

Expected:
✓ Finds latest from that sender
✓ Generates reply
```

#### C. By Index from Search Context
```
1. Say: "search for emails from alice"
   (System stores results in memory)

2. Say: "reply to first email"

Expected:
✓ Gets stored search results from memory
✓ Identifies search_results[0]
✓ Generates reply for that email

Alternative indices:
- "reply to second email" → index[1]
- "reply to last email" → index[-1]
- "reply to 3rd email" → index[2]
```

#### D. By Direct ID
```
1. Say: "reply to email id 12345"

Expected:
✓ Finds email with id=12345
✓ Generates reply
```

---

## Test 3: Context Awareness (Memory Integration)
**What was broken**: No connection between email search and reply  
**What's fixed**: Email search stores results in conversation memory

### Test Steps
```
1. Say: "search for emails about project status"
   System: Finds 5 matching emails in memory

2. Say: "reply to the first one"
   System: Uses memory to identify emails[0]

3. Say: "also reply to the 3rd one"  
   System: Uses memory to identify emails[2]

Expected Throughout:
✓ Each reply correctly identifies the email from memory
✓ No need to re-specify sender or search
✓ Conversation flows naturally
```

---

## Test 4: Hallucination Prevention
**What was broken**: LLM would invent sender preferences, dates, details  
**What's fixed**: Strict prompt constraints + low temperature

### Test A: Missing Information
```
Email Content:
  From: alice@company.com
  Subject: Can you help?
  Body: Hi, are you available?

Test Query: "reply to alice"

Check Reply For:
✓ Does NOT invent availability details
✓ Does NOT make up past interactions
✓ Does NOT assume context
✓ Only references info in original email

Good Example:
"Hi Alice, I'd be happy to help. 
What do you need assistance with?"

Bad Example (should NOT happen):
"Hi Alice, I know you usually prefer 
evening meetings. I have Thursday available..."
→ HALLUCINATION (never mentioned meetings/preferences)
```

### Test B: Tone Selection
```
1. Say: "reply to alice with casual tone"

Check Reply For:
✓ Casual language (not "Dear", "Regards")
✓ Conversational ("Hey", "Thanks!")  
✓ Still grounded (no made-up info)
✓ Still professional (no slang/errors)

Example Casual Reply:
"Hey Alice, I can help with that! 
Let me know what you need. Thanks!"
```

### Test C: Context Boundary
```
Email:
  Subject: Q4 Planning
  Body: Can you attend the Q4 planning meeting?

Bad LLM (should NOT happen):
"Of course! I'll prepare my Q3 analysis 
and quarterly projections for review."
→ HALLUCINATION: No mention of analysis/projections

Good LLM (should happen):
"Yes, I can attend the Q4 planning meeting. 
When is it scheduled?"
→ GROUNDED: Only answers what was asked
```

---

## Test 5: Error Handling (Fallbacks)
**What's new**: Better error messages and suggestions

### Test A: No Email Found
```
1. Say: "reply to zzzzzzzzzz"
   (Sender doesn't exist)

Expected:
❌ "No email found matching sender"
✓ Suggestions shown:
   - Search first, then reply
   - Use email ID
   - Use latest email
```

### Test B: Ambiguous Search Results
```
1. Say: "search for emails" (no filter)
   (Might return 20 emails)

2. Say: "reply to the one about project"
   (Ambiguous - 3 emails mention "project")

Expected:
❌ "Could not identify which email"
✓ Shows recent search results with index
✓ Asks user to specify: "reply to first email"
```

---

## Test 6: Tone Variations
**What's implemented**: 4 tone options with distinct generation

### Test All Tones
```
Email:
  From: alice@company.com
  Subject: Project deadline extended
  Body: Good news! The deadline is now June 30.

1. "reply with professional tone"
   Expect: Formal language, "Thank you", "Best regards"

2. "reply with friendly tone"  
   Expect: Warm, personal, "Thanks!", "Cheers"

3. "reply with casual tone"
   Expect: Relaxed, "Got it!", "Thanks mate"

4. "reply with formal tone"
   Expect: Very formal, "I acknowledge", "Respectfully"
```

---

## Test 7: Integration Test (Full Flow)
**Complete end-to-end test**

### Test Scenario
```
Step 1: USER
"Search for recent emails from alice and bob"

SYSTEM RESPONSE
✓ Intent: EMAIL_SEARCH
✓ Searches mailbox
✓ Returns 7 matching emails
✓ Stores results in memory
✓ Shows: "Found 7 emails from alice and bob"

---

Step 2: USER
"Reply to the first one in friendly tone"

SYSTEM RESPONSE  
✓ Intent: EMAIL_REPLY
✓ Gets stored search results from memory
✓ Identifies emails[0]
✓ Extracts tone: friendly
✓ Generates reply with friendly greeting
✓ Shows draft:
   📧 DRAFT REPLY (Friendly Tone)
   From: alice@company.com
   Subject: Project Update
   
   Your Reply:
   Hey Alice! Thanks so much for the update...
   
   Ready to send? Say "yes" or "send it"

---

Step 3: USER
"Reply to the second one with professional tone"

SYSTEM RESPONSE
✓ Reuses stored search results from memory
✓ Identifies emails[1]  
✓ Different tone: professional
✓ Shows different draft (formal language)

---

Step 4: USER
"Now reply to the last one"

SYSTEM RESPONSE
✓ Reuses stored search results
✓ Identifies emails[-1]
✓ Default tone: professional (not specified)
✓ Shows draft
```

---

## Validation Checklist

- [ ] Intent classification: "reply to email" → EMAIL_REPLY (not EMAIL_SEARCH)
- [ ] Email selection: "reply to first email" finds correct email from search results
- [ ] Memory integration: No need to re-search between replies
- [ ] Hallucination prevention: Reply only mentions info from original email
- [ ] Tone options: All 4 tones (professional, friendly, casual, formal) work
- [ ] Error handling: Clear messages when email not found
- [ ] Fallback: System suggests how to fix ambiguous requests
- [ ] Performance: Reply generation <5 seconds
- [ ] Draft storage: Draft saved for sending with "yes" confirmation

---

## Common Issues & Fixes

### Issue: "No email found to reply to"
- [ ] Verify emails exist in mailbox
- [ ] Try: "reply to latest email"
- [ ] If still fails: Check IMAP connection, email cache

### Issue: Reply mentions wrong email details
- [ ] Verify original email body loaded completely
- [ ] Check IMAP fetch returned full email content
- [ ] Retry with specific sender: "reply to alice@company.com"

### Issue: Generated reply is too short/long
- [ ] Tone impacts length (formal=longer, casual=shorter)
- [ ] Email body length affects reply length
- [ ] LLM constraint: 150-200 words max

### Issue: Index selection not working ("reply to first")
- [ ] First need: "search for emails"
- [ ] System must store results in memory
- [ ] Then use index: "reply to first email"
- [ ] Check logs: "Stored X email search results in memory"

---

## Debug Commands

### 1. Force Intent Classification Check
```bash
# In your test script:
from core.intent_classifier import intent_classifier

result = intent_classifier.classify("reply to alice")
print(f"Intent: {result}")  # Should print: EMAIL_REPLY
```

### 2. Test Email Selection
```bash
from agents.knowledge.email_reply_agent_v2 import find_target_email
from memory.conversation_memory import conversation_memory

# Simulate search results in memory
search_results = [
    {"id": 1, "from": "alice@company.com", "subject": "Project A"},
    {"id": 2, "from": "bob@company.com", "subject": "Project B"},
]
conversation_memory.set_last_email_search_results(search_results)

# Test email selection
email = find_target_email("reply to first", search_results=search_results)
print(f"Selected: {email.get('from')} - {email.get('subject')}")
```

### 3. Test Strict Prompt
```bash
from agents.knowledge.email_reply_agent_v2 import _build_strict_reply_prompt

prompt = _build_strict_reply_prompt(
    from_addr="alice@company.com",
    subject="Test Email",
    body="Can you help me?",
    tone="professional"
)
print(prompt)  # Review for hallucination constraints
```

### 4. Monitor Memory Storage
```bash
from memory.conversation_memory import conversation_memory

# Check stored results
results = conversation_memory.get_last_email_search_results()
print(f"Stored emails in memory: {len(results)}")
for email in results:
    print(f"  - {email.get('from')}: {email.get('subject')}")
```

---

## Success Criteria

All of these should be TRUE:

1. **Intent Classification**: "reply to..." correctly identified as EMAIL_REPLY
2. **Email Selection**: System finds the right email using search results
3. **No Hallucination**: Reply ONLY mentions info from original email
4. **Context Memory**: Can chain multiple replies without re-searching
5. **Error Handling**: Clear guidance when email not found
6. **Tone Support**: All 4 tones produce appropriately styled replies
7. **Safety**: Requires "yes" confirmation before sending

---

**Test Date**: ___________  
**Tester**: ___________  
**Result**: ✓ PASS / ✗ FAIL

**Notes**:  
```
[Add any observations, issues, or improvements here]
```

---

*For full details, see: [EMAIL_REPLY_FIXES.md](EMAIL_REPLY_FIXES.md)*
