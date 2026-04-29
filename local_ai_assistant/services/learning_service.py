"""
services/learning_service.py
==============================
Lightweight user-behaviour learning layer.

What it learns
--------------
* Writing style and tone preferences (formal / casual / bullet-point)
* Frequently used action sequences (e.g. "find file → summarize")
* Explicit and implicit feedback on assistant outputs
* Per-domain preferences (email style, document detail level)

Storage
-------
All data is persisted in ``data/user_preferences.json`` — a small JSON
file that loads instantly and never requires a database server.

LLM prompt injection
--------------------
``learning_service.build_style_hint()`` returns a concise natural-language
description of the user's preferences that can be appended to any LLM
system prompt, so every generated output reflects learned style.

Usage::

    from services.learning_service import learning_service

    # Record explicit positive feedback
    learning_service.record_feedback(
        query    = "summarize report",
        output   = "The report contains…",
        accepted = True,
    )

    # Record that the user edited the output (implicit negative feedback)
    learning_service.record_edit(
        original = "Here is the summary…",
        edited   = "Short, bullet-point version…",
        domain   = "document",
    )

    # Record an action sequence for future multi-step planning hints
    learning_service.record_action_sequence(["RETRIEVAL", "SUMMARY", "EMAIL_REPLY"])

    # Inject preferences into an LLM system prompt
    hint = learning_service.build_style_hint()
    # → "The user prefers concise bullet-point answers. …"

    # Get the preferred tone for a domain
    tone = learning_service.preferred_tone("email")
    # → "formal" | "casual" | "neutral"
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger
from configs.settings import PROJECT_ROOT, DATA_DIR

log = get_logger(__name__)

_PREFS_PATH: Path = Path(DATA_DIR) / "user_preferences.json"

# Tone/style token patterns extracted from user edits
_BULLET_RE    = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_FORMAL_WORDS = frozenset({
    "sincerely", "regards", "hereby", "pursuant", "accordingly",
    "attached herewith", "as per", "kindly", "please find",
})
_CASUAL_WORDS = frozenset({
    "hey", "hi there", "btw", "thanks!", "cool", "great", "awesome",
    "sure thing", "no worries", "catch you later",
})


class LearningService:
    """Tracks user preferences and injects them into LLM prompts."""

    def __init__(self, prefs_path: Path = _PREFS_PATH) -> None:
        self._path = prefs_path
        self._lock = threading.Lock()
        self._data = self._load()

    # ── Schema (default structure) ────────────────────────────────────────────

    @staticmethod
    def _default() -> dict:
        return {
            "tone": {
                "global"  : "neutral",  # neutral | formal | casual
                "email"   : "neutral",
                "document": "neutral",
            },
            "format": {
                "prefer_bullets"  : False,
                "prefer_concise"  : False,
                "prefer_detailed" : False,
            },
            "action_sequences": [],   # list of [intent, intent, …] lists
            "feedback": {
                "accepted_count": 0,
                "rejected_count": 0,
                "edits": [],          # last 20 edit pairs
            },
            "domain_hints": {},       # domain → freeform hint string
            "updated_at": 0.0,
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with self._path.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                # Merge with defaults so new keys are always present
                default = self._default()
                for k, v in default.items():
                    if k not in data:
                        data[k] = v
                    elif isinstance(v, dict):
                        for sk, sv in v.items():
                            if sk not in data[k]:
                                data[k][sk] = sv
                log.debug("[Learning] Preferences loaded from %s", self._path)
                return data
            except Exception as exc:
                log.warning("[Learning] Could not load preferences: %s", exc)
        return self._default()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._data["updated_at"] = time.time()
            with self._path.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except Exception as exc:
            log.warning("[Learning] Could not save preferences: %s", exc)

    # ── Feedback recording ────────────────────────────────────────────────────

    def record_feedback(
        self,
        query: str,
        output: str,
        accepted: bool,
        domain: str = "general",
    ) -> None:
        """Record whether the user accepted or rejected an assistant output.

        Parameters
        ----------
        query    : The user's original query.
        output   : The assistant's response that was accepted / rejected.
        accepted : True if user confirmed/used the output, False if dismissed/regenerated.
        domain   : One of: 'general', 'email', 'document', 'reminder'.
        """
        with self._lock:
            if accepted:
                self._data["feedback"]["accepted_count"] += 1
                # Learn style signals from accepted outputs
                self._infer_style(output, domain, reinforced=True)
            else:
                self._data["feedback"]["rejected_count"] += 1
            self._save()
        log.debug(
            "[Learning] Feedback recorded: accepted=%s, domain=%s", accepted, domain
        )

    def record_edit(
        self,
        original: str,
        edited: str,
        domain: str = "general",
    ) -> None:
        """Record that the user modified the assistant's output.

        The delta between *original* and *edited* is analysed to infer
        style preferences (shorter = prefers concise, added bullets = prefers
        bullets, etc.).

        Parameters
        ----------
        original : The assistant's original text.
        edited   : The text after the user's edit.
        domain   : 'general' | 'email' | 'document' | 'reminder'.
        """
        with self._lock:
            edits = self._data["feedback"]["edits"]
            edits.append({
                "original": original[:300],
                "edited"  : edited[:300],
                "domain"  : domain,
                "at"      : time.time(),
            })
            # Keep only the last 20 edits
            self._data["feedback"]["edits"] = edits[-20:]

            # Infer style changes from the edit
            orig_len  = len(original.split())
            edit_len  = len(edited.split())
            orig_bull = bool(_BULLET_RE.search(original))
            edit_bull = bool(_BULLET_RE.search(edited))

            if edit_len < orig_len * 0.6:
                self._data["format"]["prefer_concise"] = True
                self._data["format"]["prefer_detailed"] = False

            if edit_bull and not orig_bull:
                self._data["format"]["prefer_bullets"] = True

            self._infer_tone(edited, domain)
            self._save()
        log.debug("[Learning] Edit recorded for domain=%s", domain)

    def record_action_sequence(self, intents: list[str]) -> None:
        """Record a multi-step intent sequence (e.g. RETRIEVAL → SUMMARY → EMAIL_REPLY).

        Useful for planner hints — when a similar query is seen the planner
        can suggest the same sequence.
        """
        if len(intents) < 2:
            return
        with self._lock:
            seqs = self._data["action_sequences"]
            seqs.append(intents)
            # Keep last 30 unique sequences
            seen: set[str] = set()
            deduped: list[list[str]] = []
            for s in reversed(seqs):
                key = "→".join(s)
                if key not in seen:
                    seen.add(key)
                    deduped.append(s)
            self._data["action_sequences"] = list(reversed(deduped))[:30]
            self._save()

    def set_domain_hint(self, domain: str, hint: str) -> None:
        """Store a free-form style hint for a domain (e.g. 'email': 'always sign off with Sandeep')."""
        with self._lock:
            self._data["domain_hints"][domain.lower()] = hint.strip()[:500]
            self._save()

    # ── Prompt injection ─────────────────────────────────────────────────────

    def build_style_hint(self, domain: str = "general") -> str:
        """Return a concise instruction string to inject into an LLM system prompt.

        Returns an empty string when no preferences are set.
        """
        with self._lock:
            tone       = self._data["tone"].get(domain) or self._data["tone"]["global"]
            bullets    = self._data["format"]["prefer_bullets"]
            concise    = self._data["format"]["prefer_concise"]
            detailed   = self._data["format"]["prefer_detailed"]
            dom_hint   = self._data["domain_hints"].get(domain.lower(), "")

        parts: list[str] = []

        if tone == "formal":
            parts.append("Use a formal, professional tone.")
        elif tone == "casual":
            parts.append("Use a friendly, conversational tone.")

        if bullets:
            parts.append("Present information as bullet points when possible.")
        elif detailed:
            parts.append("Provide detailed, comprehensive answers.")
        elif concise:
            parts.append("Keep responses concise — avoid unnecessary elaboration.")

        if dom_hint:
            parts.append(dom_hint)

        if not parts:
            return ""

        return "User style preferences: " + " ".join(parts)

    def preferred_tone(self, domain: str = "general") -> str:
        """Return 'formal', 'casual', or 'neutral' for *domain*."""
        with self._lock:
            return (
                self._data["tone"].get(domain.lower())
                or self._data["tone"]["global"]
                or "neutral"
            )

    def suggest_sequence(self, first_intent: str) -> Optional[list[str]]:
        """Return the most common sequence that starts with *first_intent*, or None."""
        with self._lock:
            seqs = [
                s for s in self._data["action_sequences"]
                if s and s[0] == first_intent
            ]
        if not seqs:
            return None
        # Return the most frequently seen one
        from collections import Counter
        key_map = {"→".join(s): s for s in seqs}
        most_common_key, _ = Counter("→".join(s) for s in seqs).most_common(1)[0]
        return key_map.get(most_common_key)

    def summary(self) -> dict:
        """Return a compact summary of current preferences."""
        with self._lock:
            fb = self._data["feedback"]
            total = fb["accepted_count"] + fb["rejected_count"]
            return {
                "tone"         : self._data["tone"],
                "format"       : self._data["format"],
                "domain_hints" : self._data["domain_hints"],
                "accept_rate"  : round(fb["accepted_count"] / total, 2) if total else None,
                "edits_stored" : len(fb["edits"]),
                "sequences"    : len(self._data["action_sequences"]),
            }

    # ── Internal style inference ──────────────────────────────────────────────

    def _infer_style(self, text: str, domain: str, reinforced: bool) -> None:
        """Update format/tone prefs based on style signals in *text*."""
        if reinforced:
            if _BULLET_RE.search(text):
                self._data["format"]["prefer_bullets"] = True
            if len(text.split()) < 60:
                self._data["format"]["prefer_concise"] = True
        self._infer_tone(text, domain)

    def _infer_tone(self, text: str, domain: str) -> None:
        t_lower = text.lower()
        formal_hits = sum(1 for w in _FORMAL_WORDS if w in t_lower)
        casual_hits = sum(1 for w in _CASUAL_WORDS if w in t_lower)
        if formal_hits > casual_hits:
            self._data["tone"][domain] = "formal"
            self._data["tone"]["global"] = "formal"
        elif casual_hits > formal_hits:
            self._data["tone"][domain] = "casual"


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

learning_service = LearningService()
