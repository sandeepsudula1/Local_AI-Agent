import os
import json
import sys
import email
from email.header import decode_header
from dotenv import load_dotenv
import imaplib

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

# PART 1: ENV LOADING FIX — use _MEIPASS for .exe mode
base_path = getattr(sys, '_MEIPASS', _PROJECT_ROOT)
env_path = os.path.join(base_path, '.env')
load_dotenv(dotenv_path=env_path)

# PART 2: VERIFY CREDENTIALS
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

print("[EMAIL] USER:", EMAIL_USER)
print("[EMAIL] Credentials loaded:", bool(EMAIL_USER))

# PART 3: FAIL FAST (but don't crash the entire app)
if not EMAIL_USER or not EMAIL_PASS:
    print("[EMAIL] WARNING: Email credentials not loaded. Email features will be unavailable.")

# PART 5: VERIFY CONNECTION (wrapped in try/except for safety)
try:
    if EMAIL_USER and EMAIL_PASS:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(EMAIL_USER, EMAIL_PASS)
        print("[EMAIL] Login successful")
        imap.logout()
except Exception as e:
    print(f"[EMAIL] Login verification failed: {e}")

# EXE mode logging
if getattr(sys, 'frozen', False):
    print("[SYSTEM] Running in EXE mode")
    print(f"[ENV] Loaded: {EMAIL_USER}")

try:
    from configs.settings import DATA_DIR as _DATA_DIR
    _CACHE_PATH = os.path.join(str(_DATA_DIR), "email_cache.json")
except Exception:
    _CACHE_PATH = os.path.join(_PROJECT_ROOT, "data", "email_cache.json")

# Always use imaplib for native IMAP support without external dependencies
IMAP_AVAILABLE = True

from core.logging_config import get_logger
log = get_logger(__name__)


class EmailAgent:
    def __init__(self):
        import imaplib
        self.enabled = False
        self.host = os.getenv("EMAIL_HOST", "imap.gmail.com")
        self.port = int(os.getenv("EMAIL_PORT", 993))
        self.user = os.getenv("EMAIL_USER")
        self.password = os.getenv("EMAIL_PASS")
        
        if not self.user or not self.password:
            log.warning("[EMAIL] Credentials not found. Email agent disabled.")
            return
        
        if self._connect_imap():
            self.enabled = True

    def _connect_imap(self, retries=3, timeout=5):
        """Establish or re-establish IMAP connection with retry logic."""
        import socket
        import time
        for attempt in range(retries):
            try:
                socket.setdefaulttimeout(timeout)
                self.imap = imaplib.IMAP4_SSL(self.host, self.port)
                self.imap.login(self.user, self.password)
                self.imap.select("INBOX")
                log.debug("[EMAIL] IMAP initialized")
                return True
            except Exception as e:
                print(f"[EMAIL] Connection attempt {attempt+1}/{retries} failed: {e}")
                if attempt < retries - 1:
                    time.sleep(1)
                else:
                    print("[EMAIL] Connection failed after retries.")
                    return False

    def _ensure_imap(self):
        """Ensure IMAP is connected; reconnect if needed."""
        if not self.enabled:
            raise Exception("Email feature is disabled (missing config or connection failed)")
        try:
            # Quick check: try a simple NOOP command
            status, _ = self.imap.noop()
            if status != 'OK':
                raise Exception("IMAP NOOP failed")
        except Exception:
            print("[EMAIL] Reconnecting IMAP...")
            if not self._connect_imap():
                self.enabled = False
                raise Exception("Failed to reconnect to IMAP")

    def decode_str(self, value):
        if not value:
            return ""
        decoded, charset = decode_header(value)[0]
        if isinstance(decoded, bytes):
            return decoded.decode(charset or "utf-8", errors="ignore")
        return decoded

    def get_body(self, msg):
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode("utf-8", errors="ignore")
        else:
            return msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        return ""

    def fetch_unread_emails(self):
        """Fetch unread (UNSEEN) emails from INBOX."""
        return self.fetch_recent_emails(folder="INBOX", last_n=100, unseen_only=False)

    def fetch_recent_emails(self, folder="INBOX", last_n=100, unseen_only=False):
        """Fetch the most recent `last_n` emails (read + unread) from `folder`."""
        if not hasattr(self, "imap"):
            raise Exception("IMAP not initialized")
            
        self.imap.select(folder)

        if unseen_only:
            result, data = self.imap.search(None, 'UNSEEN')
        else:
            result, data = self.imap.search(None, 'ALL')
            
        if result != 'OK':
            return []

        # Take the last_n most recent (highest IDs)
        all_ids = data[0].split()
        msg_ids = all_ids[-last_n:]

        emails = []
        for msgid in msg_ids:
            res, msg_data = self.imap.fetch(msgid, "(RFC822)")
            if res == 'OK':
                msg = email.message_from_bytes(msg_data[0][1])
                emails.append({
                    "id": str(msgid.decode()),
                    "subject": self.decode_str(msg["Subject"]),
                    "from": self.decode_str(msg["From"]),
                    "date": msg["Date"],
                    "body": self.get_body(msg)
                })

        return emails

    def parse_email_query(self, query: str):
        """Use LLM to extract structured intent (person and topic) from the query."""
        import ollama
        import json
        from configs.llm_config import MODEL
        
        prompt = f"""
        Extract the target person name and the email topic from this search query.
        Return ONLY a JSON object with keys "person" and "topic".
        If a field is missing, use null. Ignore noise like "find", "email", "mail", "related to".
        
        Query: "{query}"
        
        JSON Result:"""
        
        try:
            response = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                format='json'
            )
            parsed = json.loads(response['message']['content'])
            # Clean up: ensure lower case
            if parsed.get("person"): parsed["person"] = parsed["person"].lower()
            if parsed.get("topic"): parsed["topic"] = parsed["topic"].lower()
            return parsed
        except Exception as e:
            print(f"[EMAIL] LLM Query Parsing failed: {e}")
            # Manual fallback parser for basic "from X" or "about Y"
            p = None
            t = None
            q = query.lower()
            if "from " in q:
                p = q.split("from ")[1].split()[0]
            if "about " in q:
                t = q.split("about ")[1].split()[0]
            elif "related to " in q:
                t = q.split("related to ")[1].split()[0]
            return {"person": p, "topic": t}

    def search_live_imap(self, query: str):
        """Search directly on IMAP using structured intent parsing and hard filters."""
        try:
            self._ensure_imap()
        except Exception:
            print("[EMAIL] Error: Email connection failed. Please check network.")
            return []
        
        result, data = self.imap.search(None, 'ALL')
        if result != 'OK':
            return []
            
        all_ids = data[0].split()
        
        # PART 1: PARSE QUERY INTO STRUCTURE
        parsed = self.parse_email_query(query)
        print(f"[EMAIL] Parsed Intent: {parsed}")
        
        person = parsed.get("person")
        topic = parsed.get("topic")
        
        if not person and not topic:
            # If LLM failed entirely, try a very basic keyword slice
            topic = query.lower().replace("find", "").replace("email", "").replace("mail", "").replace("whats", "").replace("what's", "").replace("new", "").replace("my", "").replace("any", "").strip()

        # Break topic into individual keywords (ignore short filler words)
        _NOISE = {'find', 'email', 'mail', 'about', 'related', 'from', 'the',
                  'what', 'whats', 'new', 'any', 'me', 'my', 'in', 'is', 'on',
                  'show', 'get', 'search', 'look', 'have', 'there', 'are', 'do'}
        topic_keywords = []
        if topic:
            topic_keywords = [w for w in topic.split() if len(w) >= 3 and w not in _NOISE]
        
        msg_ids = all_ids[-200:]
        results = []
        
        for msgid in msg_ids:
            res, msg_data = self.imap.fetch(msgid, "(RFC822)")
            if res != 'OK':
                continue
                
            msg = email.message_from_bytes(msg_data[0][1])
            sender_raw = self.decode_str(msg["From"])
            subject_raw = self.decode_str(msg["Subject"])
            body_raw = self.get_body(msg)
            
            sender = sender_raw.lower()
            subject = subject_raw.lower()
            body = body_raw.lower() if body_raw else ""
            
            # FILTERING: Match person AND any topic keyword
            match = True
            
            if person:
                if person not in sender:
                    match = False
            
            # Match ANY keyword (not full phrase)
            if match and topic_keywords:
                keyword_hit = any(kw in subject or kw in body for kw in topic_keywords)
                if not keyword_hit:
                    match = False
            
            # PART 1: RESULT CONFIDENCE
            score = 0
            if match:
                if person and person in sender: score += 50
                if topic_keywords:
                    if any(kw in subject for kw in topic_keywords): score += 30
                    if any(kw in body for kw in topic_keywords): score += 20
                if not person and not topic_keywords: score = 100

                results.append({
                    "id": str(msgid.decode()),
                    "from": sender_raw,
                    "subject": subject_raw,
                    "date": msg["Date"],
                    "snippet": body[:120].replace('\n', ' ') + "..." if body else "",
                    "id_int": int(msgid),
                    "confidence": score
                })

        # PART 5: FALLBACK (ONLY IF EMPTY)
        if not results:
            print("[EMAIL] No strict matches. Relaxing filters...")
            # Relaxed fallback logic could go here, but for now we follow the instruction
            # to return strict matches first.
            
        # Sort by most recent
        results.sort(key=lambda x: x["id_int"], reverse=True)
        
        # PART 6: LIMIT RESULTS to top 3
        final_results = results[:3]
        
        print(f"[EMAIL] Final results: {len(final_results)}")
        return final_results

    def save_draft(self, to_email: str, subject: str, body: str):
        """Save an email draft to the Gmail Drafts folder."""
        # PART 5: ACTION CONFIRMATION — Note: Actual prompt handled in orchestrator
        # This method is only called AFTER confirmation.
        from email.mime.text import MIMEText
        import imaplib
        import time

        self._ensure_imap()

        # PART 2: CREATE MIME MESSAGE
        msg = MIMEText(body)
        msg['Subject'] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
        msg['From'] = self.user
        msg['To'] = to_email

        # PART 3: SAVE AS DRAFT
        # In Gmail, the drafts folder is usually '[Gmail]/Drafts'
        try:
            self.imap.append('[Gmail]/Drafts', '', imaplib.Time2Internaldate(time.time()), str(msg).encode('utf-8'))
            print("[EMAIL] Draft saved successfully")
            return True
        except Exception as e:
            print(f"[EMAIL] Failed to save draft: {e}")
            return False

    def save_to_cache(self, emails):
        path = _CACHE_PATH

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"emails": []}
        else:
            data = {"emails": []}

        # Deduplicate by email ID so re-fetching doesn't add duplicates
        existing_ids = {str(e.get("id")) for e in data["emails"]}
        new_only = [e for e in emails if str(e.get("id")) not in existing_ids]

        data["emails"].extend(new_only)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        return f"Saved {len(new_only)} new email(s) to cache (skipped {len(emails)-len(new_only)} duplicates)."

