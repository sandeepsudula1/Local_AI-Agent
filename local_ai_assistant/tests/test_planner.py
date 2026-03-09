"""
tests/test_planner.py
======================
Unit tests for ``agents.core.planner_agent.decide_intent``.

All tests use the regex fast-path only (no LLM calls) by monkeypatching
``_llm_classify`` to return ``None``, which forces fallback to regex.

If a regex fast-path is supposed to handle a pattern, the test will pass
even when Ollama is not running.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Parametrized cases: (user_input, expected_intent)
# ---------------------------------------------------------------------------

REGEX_CASES: list[tuple[str, str]] = [
    # Greetings
    ("hello", "GREETING"),
    ("hi there!", "GREETING"),
    ("good morning", "GREETING"),
    # Short conversational → CHAT
    ("yes", "CHAT"),
    ("ok", "CHAT"),
    ("sure", "CHAT"),
    # Pure CHAT queries
    ("tell me a joke", "CHAT"),
    ("how are you?", "CHAT"),
    ("what is your name?", "CHAT"),
    # Time & date
    ("what time is it", "TIME"),
    ("what is the current time", "TIME"),
    ("what is today's date", "DATE"),
    ("what date is it today", "DATE"),
    # Reminder set
    ("remind me at 15:30 to call Alice", "REMINDER_SET"),
    ("set a reminder for tomorrow at 9am", "REMINDER_SET"),
    ("add a reminder: meeting at 3pm", "REMINDER_SET"),
    # Reminder list
    ("show my reminders", "REMINDER_LIST"),
    ("list all reminders", "REMINDER_LIST"),
    ("what reminders do I have", "REMINDER_LIST"),
    # Reminder delete
    ("delete the reminder for Alice", "REMINDER_DELETE"),
    ("remove reminder 1", "REMINDER_DELETE"),
    # Email summary
    ("summarize my emails", "EMAIL_SUMMARY"),
    ("inbox summary", "EMAIL_SUMMARY"),
    ("summarise emails", "EMAIL_SUMMARY"),
    # Email search
    ("search my emails for invoice", "EMAIL_SEARCH"),
    ("find emails from Alice", "EMAIL_SEARCH"),
    ("show emails about the meeting", "EMAIL_SEARCH"),
    # Document list
    ("list all documents", "DOCUMENT_LIST"),
    ("show me the files", "DOCUMENT_LIST"),
    ("what documents do I have", "DOCUMENT_LIST"),
    # Summary
    ("summarize the documents", "SUMMARY"),
    ("give me a summary", "SUMMARY"),
    # Topic
    ("what topics are in the documents", "TOPIC"),
    ("what themes are covered", "TOPIC"),
    # Retrieval — company facts
    ("how many employees in 2024", "RETRIEVAL"),
    ("what is the company revenue", "RETRIEVAL"),
    ("when will the company expand into robotics", "RETRIEVAL"),
    ("what is the internship duration", "RETRIEVAL"),
    # Retrieval — file references
    ("describe the company_data.csv file", "RETRIEVAL"),
    ("what does the report.pdf say", "RETRIEVAL"),
    ("describe the PNG file", "RETRIEVAL"),
    ("what is shown in the image document", "RETRIEVAL"),
    # Compare
    ("Python vs Java", "COMPARE"),
    ("compare Node.js and Deno", "COMPARE"),
    ("which is better, React or Vue", "COMPARE"),
    # Audio
    ("transcribe the meeting recording", "AUDIO_TRANSCRIBE"),
    ("what was discussed in the audio", "AUDIO_QUERY"),
    ("list audio files", "AUDIO_LIST"),
]


@pytest.mark.parametrize("user_input,expected", REGEX_CASES)
def test_regex_intent(user_input: str, expected: str) -> None:
    """The regex fast-path must classify the input without calling the LLM."""
    # Patch _llm_classify to return None → forces regex fallback
    with patch(
        "agents.core.planner_agent._llm_classify",
        return_value=None,
    ):
        from agents.core.planner_agent import decide_intent
        result = decide_intent(user_input)

    assert result == expected, (
        f"Input: {user_input!r}\n"
        f"Expected: {expected}\n"
        f"Got:      {result}"
    )


def test_decide_intent_returns_string() -> None:
    """decide_intent must always return a non-empty string."""
    with patch("agents.core.planner_agent._llm_classify", return_value=None):
        from agents.core.planner_agent import decide_intent
        result = decide_intent("some completely random gibberish input zxqy")
    assert isinstance(result, str)
    assert len(result) > 0


def test_decide_intent_unknown_falls_back_to_general_or_chat() -> None:
    """Unrecognised inputs must not crash and must return a valid label."""
    valid = {
        "GREETING", "TIME", "DATE",
        "REMINDER_SET", "REMINDER_LIST", "REMINDER_DELETE",
        "EMAIL_SUMMARY", "EMAIL_SEARCH",
        "DOCUMENT_LIST", "SUMMARY", "TOPIC", "RETRIEVAL",
        "AUDIO_TRANSCRIBE", "AUDIO_QUERY", "AUDIO_LIST",
        "COMPARE", "CHAT", "GENERAL",
    }
    with patch("agents.core.planner_agent._llm_classify", return_value=None):
        from agents.core.planner_agent import decide_intent
        result = decide_intent("xyzzy foo bar baz 1234")
    assert result in valid
