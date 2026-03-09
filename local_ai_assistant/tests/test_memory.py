"""Tests for memory/conversation_memory.py"""
import json
import os
import tempfile

import pytest

from memory.conversation_memory import ConversationMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem(tmp_path):
    """A fresh ConversationMemory backed by a temp file."""
    return ConversationMemory(
        max_history=10,
        persist_path=str(tmp_path / "memory.json"),
    )


# ---------------------------------------------------------------------------
# store / retrieve
# ---------------------------------------------------------------------------

class TestStoreRetrieve:
    def test_store_and_retrieve(self, mem):
        mem.store("name", "Alice")
        assert mem.retrieve("name") == "Alice"

    def test_retrieve_missing_key_returns_none(self, mem):
        assert mem.retrieve("nonexistent") is None

    def test_store_overwrites_existing(self, mem):
        mem.store("name", "Alice")
        mem.store("name", "Bob")
        assert mem.retrieve("name") == "Bob"

    def test_list_facts_empty(self, mem):
        assert mem.list_facts() == {}

    def test_list_facts_returns_all(self, mem):
        mem.store("name", "Alice")
        mem.store("job", "engineer")
        facts = mem.list_facts()
        assert facts["name"] == "Alice"
        assert facts["job"] == "engineer"


# ---------------------------------------------------------------------------
# clear_facts / clear_history / clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_facts_removes_only_facts(self, mem):
        mem.store("name", "Alice")
        mem.add_turn("user", "hello")
        mem.clear_facts()
        assert mem.list_facts() == {}
        assert len(mem.get_history()) == 1

    def test_clear_history_removes_only_history(self, mem):
        mem.store("name", "Alice")
        mem.add_turn("user", "hello")
        mem.clear_history()
        assert mem.retrieve("name") == "Alice"
        assert mem.get_history() == []

    def test_clear_removes_everything(self, mem):
        mem.store("name", "Alice")
        mem.add_turn("user", "hello")
        mem.clear()
        assert mem.list_facts() == {}
        assert mem.get_history() == []


# ---------------------------------------------------------------------------
# conversation history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_add_and_get_history(self, mem):
        mem.add_turn("user", "Hello")
        mem.add_turn("assistant", "Hi there!")
        hist = mem.get_history()
        assert len(hist) == 2
        assert hist[0]["role"] == "user"
        assert hist[1]["content"] == "Hi there!"

    def test_get_history_last_n(self, mem):
        for i in range(5):
            mem.add_turn("user", f"msg {i}")
        hist = mem.get_history(last_n=3)
        assert len(hist) == 3
        assert hist[-1]["content"] == "msg 4"

    def test_max_history_truncation(self):
        mem = ConversationMemory(max_history=3)
        for i in range(10):
            mem.add_turn("user", f"msg {i}")
        assert len(mem.get_history()) <= 3


# ---------------------------------------------------------------------------
# extract_and_store
# ---------------------------------------------------------------------------

class TestExtractAndStore:
    def test_extracts_name(self, mem):
        mem.extract_and_store("My name is Sandeep")
        assert mem.retrieve("name") == "Sandeep"

    def test_extracts_name_variant(self, mem):
        mem.extract_and_store("I am called Carlos")
        assert mem.retrieve("name") == "Carlos"

    def test_extracts_workplace(self, mem):
        mem.extract_and_store("I work at Acme Corp")
        assert mem.retrieve("workplace") == "Acme Corp"

    def test_no_false_extraction(self, mem):
        mem.extract_and_store("What is the weather today?")
        assert mem.list_facts() == {}


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_build_messages_returns_list(self, mem):
        msgs = mem.build_messages("You are an assistant.", "hello")
        assert isinstance(msgs, list)
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "hello"

    def test_facts_injected_into_system(self, mem):
        mem.store("name", "Alice")
        msgs = mem.build_messages("System prompt.", "hi")
        system_msg = next(m for m in msgs if m["role"] == "system")
        assert "Alice" in system_msg["content"]

    def test_history_injected(self, mem):
        mem.add_turn("user", "previous question")
        mem.add_turn("assistant", "previous answer")
        msgs = mem.build_messages("Sys.", "new question")
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_persists_to_disk(self, tmp_path):
        path = str(tmp_path / "mem.json")
        m1 = ConversationMemory(persist_path=path)
        m1.store("language", "Python")
        m1.add_turn("user", "hello")

        m2 = ConversationMemory(persist_path=path)
        assert m2.retrieve("language") == "Python"
        assert len(m2.get_history()) == 1

    def test_no_persist_path_does_not_crash(self):
        m = ConversationMemory(persist_path=None)
        m.store("x", "y")  # should not raise
        assert m.retrieve("x") == "y"


# ---------------------------------------------------------------------------
# facts_summary
# ---------------------------------------------------------------------------

class TestFactsSummary:
    def test_empty_summary(self, mem):
        assert mem.facts_summary() == ""

    def test_summary_contains_facts(self, mem):
        mem.store("name", "Bob")
        summary = mem.facts_summary()
        assert "Bob" in summary
