"""Tests for core/router.py"""
import pytest

from core.router import Router, router


# ---------------------------------------------------------------------------
# Router.route — known intents
# ---------------------------------------------------------------------------

class TestRouteKnownIntents:
    def test_retrieval_maps_to_document_search(self):
        assert router.route("RETRIEVAL") == "documents.search"

    def test_document_search_alias(self):
        assert router.route("DOCUMENT_SEARCH") == "documents.search"

    def test_summary_maps_to_document_summarize(self):
        assert router.route("SUMMARY") == "documents.summarize"

    def test_document_summary_alias(self):
        assert router.route("DOCUMENT_SUMMARY") == "documents.summarize"

    def test_document_list(self):
        assert router.route("DOCUMENT_LIST") == "documents.list"

    def test_topic_maps_to_document_topics(self):
        assert router.route("TOPIC") == "documents.topics"

    def test_email_search(self):
        assert router.route("EMAIL_SEARCH") == "email.search"

    def test_email_query_alias(self):
        assert router.route("EMAIL_QUERY") == "email.search"

    def test_email_summary(self):
        assert router.route("EMAIL_SUMMARY") == "email.summarize"

    def test_audio_transcribe(self):
        assert router.route("AUDIO_TRANSCRIBE") == "audio.transcribe"

    def test_audio_query(self):
        assert router.route("AUDIO_QUERY") == "audio.query"

    def test_audio_list(self):
        assert router.route("AUDIO_LIST") == "audio.list"

    def test_reminder_set(self):
        assert router.route("REMINDER") == "reminders.set"

    def test_set_reminder_alias(self):
        assert router.route("SET_REMINDER") == "reminders.set"

    def test_reminders_list(self):
        assert router.route("LIST_REMINDERS") == "reminders.list"

    def test_delete_reminder(self):
        assert router.route("DELETE_REMINDER") == "reminders.delete"

    def test_comparison(self):
        assert router.route("COMPARISON") == "system.compare"


# ---------------------------------------------------------------------------
# Router.route — LLM-only intents (should return None)
# ---------------------------------------------------------------------------

class TestRouteLlmOnlyIntents:
    @pytest.mark.parametrize("intent", [
        "CHAT", "GENERAL", "GREETING", "TIME", "DATE", "HELP", "UNKNOWN",
    ])
    def test_llm_only_intent_returns_none(self, intent):
        assert router.route(intent) is None


# ---------------------------------------------------------------------------
# Router.route — edge cases
# ---------------------------------------------------------------------------

class TestRouteEdgeCases:
    def test_empty_string_returns_none(self):
        assert router.route("") is None

    def test_none_returns_none(self):
        assert router.route(None) is None  # type: ignore[arg-type]

    def test_unknown_intent_returns_none(self):
        assert router.route("BANANA_BLENDER") is None

    def test_case_insensitive_routing(self):
        # Lowercase version of a known intent should still route
        result = router.route("email_search")
        assert result == "email.search"

    def test_mixed_case_routing(self):
        result = router.route("Retrieval")
        assert result == "documents.search"


# ---------------------------------------------------------------------------
# Router.is_llm_only
# ---------------------------------------------------------------------------

class TestIsLlmOnly:
    def test_chat_is_llm_only(self):
        assert router.is_llm_only("CHAT") is True

    def test_retrieval_is_not_llm_only(self):
        assert router.is_llm_only("RETRIEVAL") is False

    def test_unknown_is_llm_only(self):
        assert router.is_llm_only("UNKNOWN") is True

    def test_empty_is_llm_only(self):
        # Empty string not in LLM_ONLY_INTENTS → not LLM only
        assert router.is_llm_only("") is False


# ---------------------------------------------------------------------------
# Router.available_mappings
# ---------------------------------------------------------------------------

class TestAvailableMappings:
    def test_returns_dict(self):
        m = router.available_mappings()
        assert isinstance(m, dict)

    def test_contains_known_entries(self):
        m = router.available_mappings()
        assert "RETRIEVAL" in m
        assert "EMAIL_SEARCH" in m

    def test_is_copy_not_reference(self):
        m1 = router.available_mappings()
        m1["FAKE"] = "fake.tool"
        m2 = router.available_mappings()
        assert "FAKE" not in m2


# ---------------------------------------------------------------------------
# Router instantiation
# ---------------------------------------------------------------------------

class TestRouterInstantiation:
    def test_fresh_router_has_mappings(self):
        r = Router()
        assert len(r.available_mappings()) > 0

    def test_module_singleton_is_router_instance(self):
        assert isinstance(router, Router)
