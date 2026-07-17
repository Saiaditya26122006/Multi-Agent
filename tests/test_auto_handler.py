"""Tests for the AUTO workspace handler."""

import pytest
from unittest.mock import patch, MagicMock

from web.handlers.auto_handler import (
    classify_intent,
    handle_auto_message,
    format_auto_response,
)


class TestClassifyIntent:
    def test_question_detected(self):
        result = classify_intent("What's the pricing model?")
        assert result["intent"] == "question"
        assert result["confidence"] >= 0.7

    def test_command_detected(self):
        result = classify_intent("Run the financial model")
        assert result["intent"] == "command"

    def test_decision_detected(self):
        result = classify_intent("Yes")
        assert result["intent"] == "decision"

    def test_correction_detected(self):
        result = classify_intent("Actually it's per-seat, not per-department")
        assert result["intent"] == "correction"

    def test_feedback_detected(self):
        result = classify_intent("Good, I like that approach")
        assert result["intent"] == "feedback"

    def test_long_text_is_new_data(self):
        long_text = "This is a very long piece of text " * 10
        result = classify_intent(long_text)
        assert result["intent"] == "new_data"
        assert result["suggested_workspace"] == "feed"

    def test_structured_data_detected(self):
        result = classify_intent("Name|Role|Status\nAlice|CTO|Active")
        assert result["intent"] == "new_data"

    def test_ambiguous_short_text(self):
        result = classify_intent("hmm")
        assert result["intent"] == "ambiguous"
        assert result["confidence"] < 0.5


def _mock_enriched_chunk(content="Pricing is annual SaaS", source_type="ceo_doc"):
    chunk = MagicMock()
    chunk.content = content
    chunk.epistemic_status = "ASSUMPTION"
    chunk.source_type = source_type
    return {"chunk": chunk, "relationships": []}


class TestHandleAutoMessage:
    # _handle_question calls retrieve_with_relationships + generate_answer. Patching
    # rag_service.retrieve left the mock dead, so this ran against the live database
    # and made a real LLM call.
    @patch("web.handlers.llm_helper.generate_answer")
    @patch("services.rag_service.retrieve_with_relationships")
    def test_question_answered_directly(self, mock_retrieve, mock_answer):
        mock_retrieve.return_value = [_mock_enriched_chunk()]
        mock_answer.return_value = "Pricing is an annual institutional subscription."

        result = handle_auto_message("What's our pricing?")
        assert result["action"] == "direct_answer"
        assert result["intent"] == "question"
        assert result["response_text"] == "Pricing is an annual institutional subscription."

    @patch("services.rag_service.retrieve_with_relationships")
    def test_question_with_no_results_says_so(self, mock_retrieve):
        mock_retrieve.return_value = []

        result = handle_auto_message("What's our pricing?")
        assert result["action"] == "direct_answer"
        assert "No relevant data found" in result["response_text"]

    def test_decision_routes_to_pipeline(self):
        result = handle_auto_message("Yes")
        assert result["action"] == "route_to_pipeline"
        assert result["intent"] == "decision"

    def test_ambiguous_tries_rag_answer(self):
        result = handle_auto_message("hmm ok")
        assert result["action"] == "direct_answer"
        assert result["intent"] in ("ambiguous", "question")

    def test_command_suggests_workspace(self):
        result = handle_auto_message("Run the financials")
        assert result["action"] == "suggest_workspace"
        assert result["suggested_workspace"] == "build"


class TestFormatAutoResponse:
    def test_formats_response_text(self):
        result = {"response_text": "Here's what I found", "action": "direct_answer"}
        text = format_auto_response(result)
        assert text == "Here's what I found"

    def test_fallback_when_no_response(self):
        result = {"action": "route_to_pipeline", "response_text": None}
        text = format_auto_response(result)
        assert "Processing" in text
