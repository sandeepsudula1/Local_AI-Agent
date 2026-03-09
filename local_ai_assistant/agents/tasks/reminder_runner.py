import time
from datetime import datetime
import json
import os
import dateparser

try:
    from plyer import notification as _plyer_notification
    _HAS_PLYER = True
except Exception:
    _plyer_notification = None
    _HAS_PLYER = False

print("🔥 RUNNING LATEST reminder_runner.py")

# ---------------------
# FILE PATH
# ---------------------
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
REM_FILE = os.path.join(PROJECT_ROOT, "data", "reminders.json")

print("Reminder file path:", REM_FILE)


# ---------------------
# LOAD / SAVE
# ---------------------
def load_reminders():
    if os.path.exists(REM_FILE):
        try:
            with open(REM_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []


def save_reminders(rem_list):
    with open(REM_FILE, "w") as f:
        json.dump(rem_list, f, indent=4)


# ---------------------
# CHECK REMINDERS
# ---------------------
def check_reminders():
    reminders = load_reminders()
    now = datetime.now()

    for r in reminders:
        if r.get("fired"):
            continue

        t = dateparser.parse(r["time"])
        if not t:
            continue

        # Compute difference (seconds) where positive means now is after scheduled time
        diff = (now - t).total_seconds()

        # trigger if now is at or after scheduled time and within a 45-second window
        if diff >= 0 and diff <= 45:
            msg = r["text"]

            print("\n🔔 Reminder Triggered:", msg, "\n", flush=True)

            # write debug log to help identify which process triggered the toast
            try:
                log_path = os.path.join(PROJECT_ROOT, 'reminder_log.txt')
                with open(log_path, 'a', encoding='utf-8') as lf:
                    lf.write(f"{datetime.now().isoformat()} [agents/tasks/reminder_runner] Triggered: {msg}\n")
            except Exception:
                pass

            # Windows desktop notification via plyer
            _notified = False
            if _HAS_PLYER:
                try:
                    _plyer_notification.notify(
                        title="Reminder",
                        message=msg,
                        timeout=5,
                        app_name="AI Assistant",
                    )
                    _notified = True
                except Exception:
                    pass
            if not _notified:
                print(f"[Notification] Reminder: {msg}")

            r["fired"] = True
            save_reminders(reminders)


print("🔔 Reminder background service running...")

while True:
    check_reminders()
    time.sleep(5)
