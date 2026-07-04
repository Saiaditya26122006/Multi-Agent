"""Tests for the FEED workspace handler."""

import pytest
from unittest.mock import patch

from web.handlers.feed_handler import (
    detect_format,
    split_into_atomic_facts,
    handle_raw_text,
    tag_epistemic_status,
    format_feed_response,
    format_feed_panel,
    _infer_epistemic_status,
)


class TestDetectFormat:
    def test_bullets_detected(self):
        text = "- First point\n- Second point\n- Third point"
        assert detect_format(text) == "bullets"

    def test_numbered_bullets_detected(self):
        text = "1. First\n2. Second\n3. Third"
        assert detect_format(text) == "bullets"

    def test_table_detected(self):
        text = "Name|Value|Status\nA|100|Active\nB|200|Inactive"
        assert detect_format(text) == "table"

    def test_paragraph_detected(self):
        text = "This is a paragraph of text. It has multiple sentences. No special formatting."
        assert detect_format(text) == "paragraph"

    def test_mixed_detected(self):
        text = "Some context here about pricing.\nAnd another line of context.\nA third line of paragraph.\n- Bullet one\n- Bullet two"
        assert detect_format(text) == "mixed"


class TestSplitAtomicFacts:
    def test_bullets_split_correctly(self):
        text = "- Pricing is per-department\n- Annual contracts\n- SaaS model"
        facts = split_into_atomic_facts(text, "bullets")
        assert len(facts) == 3
        assert facts[0]["text"] == "Pricing is per-department"
        assert facts[0]["source_format"] == "bullets"

    def test_paragraph_splits_on_sentences(self):
        text = "Revenue comes from SaaS. Pricing is annual. Target is institutions."
        facts = split_into_atomic_facts(text, "paragraph")
        assert len(facts) == 3

    def test_table_splits_by_row(self):
        text = "Name|Role|Status\nAlice|CTO|Active\nBob|CEO|Active"
        facts = split_into_atomic_facts(text, "table")
        assert len(facts) == 2
        assert "Alice" in facts[0]["text"]

    def test_empty_text_returns_empty(self):
        facts = split_into_atomic_facts("", "paragraph")
        assert facts == []

    def test_short_items_filtered(self):
        text = "- OK\n- This is a real fact with enough content"
        facts = split_into_atomic_facts(text, "bullets")
        assert len(facts) == 1


class TestEpistemicInference:
    def test_confirmed_cues(self):
        assert _infer_epistemic_status("We confirmed pricing with the dean") == "CONFIRMED"
        assert _infer_epistemic_status("Evidence shows market demand") == "CONFIRMED"

    def test_assumption_cues(self):
        assert _infer_epistemic_status("I think the pricing is right") == "ASSUMPTION"
        assert _infer_epistemic_status("This is our hypothesis about buyers") == "ASSUMPTION"

    def test_contradiction_cues(self):
        assert _infer_epistemic_status("This contradicts the earlier claim") == "CONTRADICTION"

    def test_no_cues_returns_inferred(self):
        assert _infer_epistemic_status("Annual SaaS subscription model") == "INFERRED"


class TestHandleRawText:
    def test_returns_structured_result(self):
        text = "- Revenue is SaaS\n- Pricing is annual\n- Target is universities"
        result = handle_raw_text(text)
        assert result["format_detected"] == "bullets"
        assert result["count"] == 3
        assert len(result["facts"]) == 3


class TestTagEpistemicStatus:
    def test_returns_full_tag(self):
        result = tag_epistemic_status("I think the buyer is the research dean")
        assert result["epistemic_status"] == "ASSUMPTION"
        assert result["confidence"] == 0.8
        assert len(result["language_cues"]) > 0

    def test_no_cues_lower_confidence(self):
        result = tag_epistemic_status("Annual pricing model")
        assert result["confidence"] == 0.5
        assert result["language_cues"] == []


class TestFormatting:
    def test_format_feed_response(self):
        results = {
            "format_detected": "bullets",
            "count": 3,
            "facts": [
                {"text": "Fact one", "inferred_status": "ASSUMPTION"},
                {"text": "Fact two", "inferred_status": "CONFIRMED"},
                {"text": "Fact three", "inferred_status": "INFERRED"},
            ],
        }
        text = format_feed_response(results)
        assert "3 fact(s)" in text
        assert "[ASSUMPTION]" in text
        assert "[CONFIRMED]" in text

    def test_format_feed_response_empty(self):
        results = {"format_detected": "paragraph", "count": 0, "facts": []}
        text = format_feed_response(results)
        assert "No extractable facts" in text

    def test_format_feed_panel(self):
        results = {
            "format_detected": "bullets",
            "count": 2,
            "facts": [
                {"text": "Fact one", "inferred_status": "ASSUMPTION", "source_format": "bullets"},
                {"text": "Fact two", "inferred_status": "CONFIRMED", "source_format": "bullets"},
            ],
        }
        panel = format_feed_panel(results)
        assert panel["type"] == "feed_results"
        assert panel["total_facts"] == 2
        assert "ASSUMPTION" in panel["status_breakdown"]
