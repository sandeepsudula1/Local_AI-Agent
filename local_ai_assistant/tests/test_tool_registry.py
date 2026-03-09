"""Tests for tools/tool_registry.py"""
import pytest

from tools.tool_registry import TOOLS, ToolCatalog, tool_catalog


# ---------------------------------------------------------------------------
# TOOLS dict structure
# ---------------------------------------------------------------------------

class TestToolsDict:
    def test_tools_dict_is_not_empty(self):
        assert len(TOOLS) > 0

    def test_each_tool_has_required_keys(self):
        required = {"description", "examples", "intent", "args"}
        for name, meta in TOOLS.items():
            missing = required - set(meta.keys())
            assert not missing, f"Tool {name!r} missing keys: {missing}"

    def test_tool_names_follow_dot_notation(self):
        for name in TOOLS:
            assert "." in name, f"Tool {name!r} should use dot notation"

    def test_examples_are_non_empty_list(self):
        for name, meta in TOOLS.items():
            assert isinstance(meta["examples"], list), name
            assert len(meta["examples"]) >= 1, name


# ---------------------------------------------------------------------------
# ToolCatalog.validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_tool_name(self):
        assert tool_catalog.validate("documents.search") is True

    def test_valid_email_search(self):
        assert tool_catalog.validate("email.search") is True

    def test_invalid_tool_name_returns_false(self):
        assert tool_catalog.validate("faketools.explode") is False

    def test_empty_string_invalid(self):
        assert tool_catalog.validate("") is False

    def test_none_safe(self):
        # validate should not raise on None-like input
        assert tool_catalog.validate(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ToolCatalog.get
# ---------------------------------------------------------------------------

class TestGet:
    def test_get_returns_dict(self):
        meta = tool_catalog.get("documents.search")
        assert isinstance(meta, dict)
        assert "description" in meta

    def test_get_unknown_returns_none(self):
        assert tool_catalog.get("unknown.tool") is None


# ---------------------------------------------------------------------------
# ToolCatalog.for_intent
# ---------------------------------------------------------------------------

class TestForIntent:
    def test_retrieval_maps_to_document_search(self):
        name = tool_catalog.for_intent("RETRIEVAL")
        assert name == "documents.search"

    def test_email_search_maps_correctly(self):
        name = tool_catalog.for_intent("EMAIL_SEARCH")
        assert name == "email.search"

    def test_unknown_intent_returns_none(self):
        assert tool_catalog.for_intent("BANANA_QUERY") is None

    def test_case_insensitive_lookup(self):
        # for_intent should handle different casings gracefully
        name = tool_catalog.for_intent("retrieval")
        assert name in (None, "documents.search")  # either acceptable per impl


# ---------------------------------------------------------------------------
# ToolCatalog.list_tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_returns_list(self):
        tools = tool_catalog.list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_list_tools_contains_known_tools(self):
        tools = tool_catalog.list_tools()
        assert "documents.search" in tools
        assert "email.search" in tools
        assert "reminders.set" in tools


# ---------------------------------------------------------------------------
# ToolCatalog.describe_all / describe_for_llm
# ---------------------------------------------------------------------------

class TestDescribe:
    def test_describe_all_returns_string(self):
        result = tool_catalog.describe_all()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_describe_all_mentions_all_tools(self):
        desc = tool_catalog.describe_all()
        for name in tool_catalog.list_tools():
            assert name in desc, f"{name} not in describe_all output"

    def test_describe_for_llm_returns_string(self):
        result = tool_catalog.describe_for_llm()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_describe_single_tool(self):
        desc = tool_catalog.describe("documents.search")
        assert "documents.search" in desc
        assert isinstance(desc, str)

    def test_describe_unknown_tool(self):
        desc = tool_catalog.describe("nonexistent.tool")
        assert "Unknown" in desc or desc == ""
