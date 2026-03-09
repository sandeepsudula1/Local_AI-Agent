"""
services/reminder_service.py
=============================
Background reminder polling service.

Reads ``settings.reminders_file`` every ``settings.reminder_poll_interval``
seconds, fires desktop notifications for due reminders, and marks them fired.

Usage::

    from services.reminder_service import reminder_service

    reminder_service.start()   # starts daemon thread — call once at startup
    reminder_service.stop()    # graceful shutdown
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger
from configs.settings import settings

log = get_logger(__name__)


class ReminderService:
    """Polls reminders.json and fires notifications for due entries."""

    # Parse formats tried in order before falling back to dateparser
    _TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling daemon thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="reminder-poller",
        )
        self._thread.start()
        log.info("Reminder service started (poll interval: %ds)", settings.reminder_poll_interval)

    def stop(self) -> None:
        """Signal the polling thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=settings.reminder_poll_interval + 2)
        log.info("Reminder service stopped")

    # ── internal ───────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(timeout=settings.reminder_poll_interval):
            try:
                self._check_reminders()
            except Exception as exc:
                log.exception("Reminder poll error: %s", exc)

    def _check_reminders(self) -> None:
        rem_file: Path = settings.reminders_file
        reminders = self._load(rem_file)
        if not reminders:
            return

        now = datetime.now()
        changed = False

        for r in reminders:
            if r.get("fired"):
                continue
            due = self._parse_time(r.get("time", ""))
            if due is None:
                continue
            diff = (now - due).total_seconds()
            if 0 <= diff <= settings.reminder_fire_window:
                text = r.get("text", "Reminder")
                log.info("Firing reminder: %s", text)
                print(f"\n\U0001f514 Reminder: {text}\n", flush=True)
                self._notify(text)
                r["fired"] = True
                changed = True

        if changed:
            self._save(rem_file, reminders)

    def _parse_time(self, raw: str) -> Optional[datetime]:
        for fmt in self._TIME_FORMATS:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
        # Fallback to dateparser if available
        try:
            import dateparser
            return dateparser.parse(raw)
        except Exception:
            return None

    @staticmethod
    def _notify(text: str) -> None:
        try:
            from plyer import notification as _plyer_notif
            _plyer_notif.notify(
                title="Reminder",
                message=text,
                timeout=10,
            )
        except Exception:
            try:
                from agents.tasks.notification_agent import notify
                notify("Reminder", text)
            except Exception as exc:
                log.debug("Notification failed: %s", exc)

    @staticmethod
    def _load(path: Path) -> list:
        if not path.exists():
            return []
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            log.warning("Could not read reminders file: %s", exc)
            return []

    @staticmethod
    def _save(path: Path, reminders: list) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(reminders, fh, indent=4)
        except Exception as exc:
            log.warning("Could not save reminders file: %s", exc)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
reminder_service = ReminderService()
