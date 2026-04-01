import json
import os
import threading
from datetime import datetime, timedelta
import dateparser
import re

import os

from agents.tasks.notification_agent import notify as _notify

# In-memory scheduled timers to keep references
_scheduled_timers = []

# Correct data path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))       # /agents/tasks
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))   # project root
REM_FILE = os.path.join(PROJECT_ROOT, "data", "reminders.json")


# ===============================
# LOAD / SAVE REMINDERS
# ===============================
def load_reminders():
    if os.path.exists(REM_FILE):
        try:
            with open(REM_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []


def save_reminders(reminders):
    with open(REM_FILE, "w") as f:
        json.dump(reminders, f, indent=4)


def _schedule_single(reminder):
    """Schedule a single reminder dict {text, time, fired} to trigger notification."""
    # Accept multiple time formats (with or without seconds) and fall back to dateparser
    when = None
    tstr = reminder.get("time")
    if not tstr:
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            when = datetime.strptime(tstr, fmt)
            break
        except Exception:
            when = None

    if not when:
        try:
            when = dateparser.parse(tstr)
        except Exception:
            when = None

    if not when:
        return False

    now = datetime.now()
    delay = (when - now).total_seconds()
    # (debug prints removed)
    try:
        pass  # scheduling confirmed
    except Exception:
        pass

    # If the scheduled time is clearly in the past (more than 5 seconds), fire immediately.
    # If it's within a small window (-5s .. 1s) treat it as near-future and schedule a short delay
    # to avoid immediate-toasting due to rounding or minimal timing differences when creating reminders.
    if delay < -5:
        pass  # past reminder — fire immediately
        _notify("Reminder", reminder.get("text", "Reminder"))
        reminder["fired"] = True
        reminders = load_reminders()
        for r in reminders:
            if r.get("time") == reminder.get("time") and r.get("text") == reminder.get("text"):
                r["fired"] = True
        save_reminders(reminders)
        return True

    if delay <= 1:
        pass  # small delay — adjust to avoid immediate popup
        # schedule very shortly to allow process scheduling and avoid immediate popup
        delay = max(delay, 0.5)

    # Cap delay to avoid OverflowError in threading.Timer for far-future reminders.
    # The background poller (chat.py / smart_agent.py) will handle delivery
    # when the reminder's time finally arrives.
    _MAX_TIMER_DELAY = 7 * 24 * 3600  # 7 days
    if delay > _MAX_TIMER_DELAY:
        return True

    timer = threading.Timer(delay, lambda: _notify("Reminder", reminder.get("text", "Reminder")))
    timer.daemon = True
    timer.start()
    _scheduled_timers.append(timer)
    return True


# On import: schedule any pending reminders saved on disk
try:
    _existing = load_reminders()
    for r in _existing:
        if not r.get("fired", False):
            _schedule_single(r)
except Exception:
    pass


# ===============================
# NATURAL LANGUAGE TIME PARSING
# ===============================
def _normalise_time_format(text: str) -> str:
    """
    Pre-process the user utterance so that space-separated or dot-separated
    time expressions are converted to standard HH:MM before dateparser sees
    them.  Examples that are fixed:

        "12 35 pm"  → "12:35 pm"
        "12 35 pm today" → "12:35 pm today"
        "at 9 30"   → "at 9:30"
        "12.35 pm"  → "12:35 pm"
        "1235"      → "12:35"       (4-digit run only when near 'at')

    Already-correct formats ("12:35") pass through unchanged.
    """
    # 1. "HH MM am/pm" or "HH MM" where MM is exactly two digits (minutes)
    #    Only fires when the two numbers look like hour + minute (H 1-12/0-23, M 00-59)
    #    Anchored on word boundaries so we don't mangle "in 5 30 minutes"
    text = re.sub(
        r'(?<=\b)(\d{1,2})\s+(\d{2})\s*(am|pm)\b',
        lambda m: f"{m.group(1)}:{m.group(2)} {m.group(3)}",
        text, flags=re.I
    )
    # Same without am/pm when preceded by "at" or "@"
    text = re.sub(
        r'(?:at|@)\s+(\d{1,2})\s+(\d{2})\b',
        lambda m: f"at {m.group(1)}:{m.group(2)}",
        text, flags=re.I
    )
    # 2. Dot-separated: "12.35" → "12:35" (only when plausible time values)
    text = re.sub(
        r'\b(\d{1,2})\.(\d{2})\s*(am|pm)?\b',
        lambda m: f"{m.group(1)}:{m.group(2)}{(' ' + m.group(3)) if m.group(3) else ''}",
        text, flags=re.I
    )
    # 3. 4-digit run "1235" when preceded by "at" → "12:35"
    text = re.sub(
        r'(?:at|@)\s+(\d{2})(\d{2})\b',
        lambda m: f"at {m.group(1)}:{m.group(2)}",
        text, flags=re.I
    )
    return text


def extract_reminder_details(text):
    # Normalise non-standard time formats BEFORE anything else
    text = _normalise_time_format(text.strip())
    original = text.strip()

    # Extract message FIRST (preserve casing for message)
    msg = original
    msg = re.sub(r"\b(remind me to|remind me|remind|please|set)\b", "", msg, flags=re.I).strip()

    # 1) Relative expressions: in 10 minutes / after 10 minutes / in 2 hours
    rel = re.search(r"\b(?:in|after)\s+(\d+)\s+(minute|minutes|hour|hours|sec|second|seconds)\b", original, flags=re.I)
    now = datetime.now()
    parsed = None
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        if "minute" in unit:
            parsed = now + timedelta(minutes=amount)
        elif "hour" in unit:
            parsed = now + timedelta(hours=amount)
        elif "sec" in unit or "second" in unit:
            parsed = now + timedelta(seconds=amount)

    # 2) Explicit HH:MM with optional date
    if not parsed:
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s+at\s+(\d{1,2}:\d{2})", original)
        if m:
            date_str = m.group(1)
            time_str = m.group(2)
            try:
                parsed = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            except Exception:
                parsed = None

    if not parsed:
        # try HH:MM today/tomorrow
        m2 = re.search(r"\b(at\s+)?(\d{1,2}:\d{2})\b", original)
        if m2:
            time_str = m2.group(2)
            today = datetime.now().date()
            try:
                candidate = datetime.strptime(f"{today} {time_str}", "%Y-%m-%d %H:%M")
                # if time already passed, schedule for next day
                if candidate <= now:
                    candidate = candidate + timedelta(days=1)
                parsed = candidate
            except Exception:
                parsed = None

    # explicit 'tomorrow' / 'today' handling
    if not parsed:
        if re.search(r"\btomorrow\b", original, flags=re.I):
            # look for time in the string
            mtime = re.search(r"(\d{1,2}(:\d{2})?\s*(am|pm)?)", original, flags=re.I)
            if mtime:
                t = dateparser.parse(mtime.group(1))
                if t:
                    candidate = datetime.combine((now + timedelta(days=1)).date(), t.time())
                    parsed = candidate
        elif re.search(r"\btoday\b", original, flags=re.I):
            mtime = re.search(r"(\d{1,2}(:\d{2})?\s*(am|pm)?)", original, flags=re.I)
            if mtime:
                t = dateparser.parse(mtime.group(1))
                if t:
                    candidate = datetime.combine(now.date(), t.time())
                    if candidate <= now:
                        candidate = candidate + timedelta(days=1)
                    parsed = candidate

    # 3) Fallback: try to find date/time substrings using dateparser's search
    # This handles many natural-language forms like 'next monday at 9am',
    # 'in 10 minutes', 'tomorrow at 5pm', etc.
    if not parsed:
        try:
            from dateparser.search import search_dates

            results = search_dates(original, settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now})
            if results:
                # pick the last detected date expression (often the explicit time)
                match_text, match_dt = results[-1]
                parsed = match_dt
                # remove the matched text from the message so it doesn't appear in the reminder text
                try:
                    # remove exact matched substring (case-insensitive)
                    pattern = re.escape(match_text)
                    msg = re.sub(pattern, "", msg, flags=re.I)
                except Exception:
                    msg = msg.replace(match_text, "")
                # remove common leftover time-connectors and polite words that may remain
                msg = re.sub(r"\b(?:at|on|in|for|to|this|that|next|tomorrow|today|please|remind|remind me|set|a|an|the)\b", "", msg, flags=re.I)
                # collapse whitespace and punctuation
                msg = re.sub(r"[\s,;:\-]+", " ", msg).strip()
        except Exception:
            try:
                parsed = dateparser.parse(
                    original,
                    settings={
                        "PREFER_DATES_FROM": "future",
                        "RELATIVE_BASE": now
                    }
                )
            except Exception:
                parsed = None

    if not parsed:
        return None, None

    # Store seconds as well to avoid minute-rounding causing near-immediate firing
    final_time = parsed.strftime("%Y-%m-%d %H:%M:%S")

    # Clean message further (remove time expressions and leftover prepositions/connectors)
    msg = re.sub(r"\b(?:in|after)\s+\d+\s+(minutes|minute|hours|hour|seconds|second)\b", "", msg, flags=re.I)
    msg = re.sub(r"\b(at\s+)?\d{1,2}:\d{2}\s*(am|pm)?\b", "", msg, flags=re.I)
    msg = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", msg)
    # Strip orphaned am/pm and date-anchor words left after time removal
    msg = re.sub(r"\b(am|pm|today|tomorrow|yesterday|tonight|morning|afternoon|evening|night)\b", "", msg, flags=re.I)
    # Strip trailing/leading prepositions left over after date/time removal
    msg = re.sub(r"\b(?:on|at|in|for|to|by|from)\s*$", "", msg.strip(), flags=re.I)
    msg = re.sub(r"^\s*(?:on|at|in|for|to|by|from)\b", "", msg, flags=re.I)
    msg = msg.strip(" ,.")
    msg = " ".join(msg.split())

    if msg == "":
        msg = "Reminder"

    return msg, final_time


# ===============================
# ADD / LIST / DELETE REMINDERS
# ===============================
def add_reminder(text, time):
    if not time:
        return "I could not understand the reminder time."

    reminders = load_reminders()

    reminders.append({
        "text": text,
        "time": time,
        "fired": False
    })

    save_reminders(reminders)
    # Do NOT schedule a local timer by default when adding a reminder from an
    # interactive session. The background `reminder_runner.py` is responsible
    # for delivering notifications at the correct time. This avoids an
    # immediate toast being shown in the process that added the reminder.
    #
    # If you do want inline scheduling in the current process, set
    # environment variable `ENABLE_INLINE_SCHEDULING=1`.
    if os.getenv("ENABLE_INLINE_SCHEDULING", "0") == "1":
        try:
            _schedule_single({"text": text, "time": time, "fired": False})
        except Exception:
            pass
    return "Reminder added successfully."


def list_reminders(check_only=False):
    reminders = load_reminders()

    if check_only:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        due = []

        for r in reminders:
            if r["time"] == now and not r.get("fired", False):
                due.append(r["text"])
                r["fired"] = True

        save_reminders(reminders)
        return due

    if not reminders:
        return "No reminders found."

    # Format as human-readable list grouped by fired status
    lines = []
    pending = [r for r in reminders if not r.get("fired", False)]
    past    = [r for r in reminders if r.get("fired", False)]

    if pending:
        lines.append("Upcoming reminders:")
        for r in pending:
            lines.append(f"  - [{r.get('time', '?')}] {r.get('text', 'Reminder')}")
    if past:
        lines.append("Past / fired reminders:")
        for r in past[-5:]:  # only show the 5 most recent past ones
            lines.append(f"  - [{r.get('time', '?')}] {r.get('text', 'Reminder')} (done)")

    return "\n".join(lines)


def delete_reminder(text):
    reminders = load_reminders()
    new_list = [r for r in reminders if text.lower() not in r["text"].lower()]
    save_reminders(new_list)
    return "Reminder deleted successfully."


def handle_delete_reminder(user_input: str) -> str:
    """Parse, fuzzy-match, and delete a reminder from natural-language input.

    Single match  → delete immediately and confirm.
    Multiple matches → return a numbered list asking the user to pick.
    No match      → return a helpful 'not found' message.
    """
    reminders = load_reminders()
    if not reminders:
        return "You have no reminders to delete."

    # Strip common delete/cancel prefixes to extract the search keyword
    q = user_input.lower().strip()
    for prefix in (
        "delete reminder", "remove reminder", "cancel reminder",
        "delete the reminder", "remove the reminder",
        "delete", "remove", "cancel",
    ):
        if q.startswith(prefix):
            q = q[len(prefix):].strip()
            break

    if not q:
        # No keyword provided — list all reminders for the user to choose
        lines = ["Which reminder should I delete?"]
        for i, r in enumerate(reminders, 1):
            lines.append(f"  {i}. [{r.get('time', '?')}] {r.get('text', '')}")
        return "\n".join(lines)

    # Build a set of meaningful words from the query (≥3 chars, skip stopwords)
    _STOP = {"the", "a", "an", "my", "this", "that", "to", "for", "me", "i"}
    q_words = {w for w in re.findall(r"\w+", q) if len(w) >= 3 and w not in _STOP}

    matched = []
    for r in reminders:
        r_text = r.get("text", "").lower()
        # Direct substring match
        if q in r_text:
            matched.append(r)
            continue
        # Word-level match: any meaningful query word found in reminder text
        if q_words and any(w in r_text for w in q_words):
            matched.append(r)

    if not matched:
        return f"No reminder matching '{q}' was found."

    if len(matched) == 1:
        r = matched[0]
        new_list = [
            x for x in reminders
            if not (x.get("text") == r.get("text") and x.get("time") == r.get("time"))
        ]
        save_reminders(new_list)
        return f"Deleted reminder: '{r.get('text', '')}' scheduled at {r.get('time', '?')}."

    # Multiple matches — ask the user to confirm which one
    lines = [f"Multiple reminders match '{q}'. Which one should I delete?"]
    for i, r in enumerate(matched, 1):
        lines.append(f"  {i}. [{r.get('time', '?')}] {r.get('text', '')}")
    lines.append("Reply with the number or exact text to confirm deletion.")
    return "\n".join(lines)


def handle_set_reminder(user_text):
    """Helper for `smart_agent.py` to parse and schedule one or more reminders from free text.
    Supports multiple reminders separated by ';' or ' and then ' or comma when time expressions present.
    Returns (success, message)
    """
    # Split into segments for multiple reminders
    parts = re.split(r"[;\n]", user_text)
    scheduled = 0
    errors = 0
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # If ' and ' connects multiple reminders with time expressions, attempt secondary split
        if ' and ' in part.lower() and re.search(r"\b(at|in|after|tomorrow|today|on)\b", part, flags=re.I):
            subparts = re.split(r"\band then\b|\band\b", part, flags=re.I)
        else:
            subparts = [part]

        for sp in subparts:
            sp = sp.strip()
            if not sp:
                continue
            msg, rtime = extract_reminder_details(sp)
            if not rtime:
                errors += 1
                continue
            add_reminder(msg, rtime)
            scheduled += 1

    if scheduled == 0:
        return False, "I could not parse any reminder time. Try 'remind me at 15:22' or 'remind me in 10 minutes'."
    return True, f"Added {scheduled} reminder(s); {errors} failed to parse." 