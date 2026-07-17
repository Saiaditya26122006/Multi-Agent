"""Tests for the FEED workspace handler."""

import pytest
from unittest.mock import patch

from web.handlers.feed_handler import (
    detect_format,
    split_into_atomic_facts,
    handle_raw_text,
    infer_epistemic_status,
    format_feed_response,
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
    def test_files_every_fact_it_was_given(self):
        """handle_raw_text no longer returns a parse of the input
        ({format_detected, count, facts}); it files the facts itself and reports
        what it did with them. Every bullet must end up either auto-filed or
        queued for review — silently dropping one loses CEO data."""
        text = "- Revenue is SaaS\n- Pricing is annual\n- Target is universities"
        result = handle_raw_text(text)

        assert result["action"] in ("auto_filed", "needs_review", "no_facts")
        accounted = int(result["auto_filed_count"]) + int(result["review_count"])
        assert accounted == 3


class TestInferEpistemicStatus:
    """tag_epistemic_status returned {epistemic_status, confidence, language_cues}.

    The audit split the four merged confidence concepts apart, so the tagger no
    longer invents a confidence number of its own — it reports the epistemic status
    and nothing else. Reliability and per-link sufficiency live in
    services/source_reliability.py and services/evidence_links.py now.
    """

    def test_hedging_language_is_an_assumption(self):
        assert infer_epistemic_status("I think the buyer is the research dean", "fact") == "ASSUMPTION"

    def test_bare_statement_is_not_confirmed_from_text_alone(self):
        # CONFIRMED requires source traceability verified externally; asserting
        # something plainly is not evidence that it is true.
        assert infer_epistemic_status("Annual pricing model", "fact") == "INFERRED"


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

    # A test for format_feed_panel lived here. No such function exists in
    # feed_handler — nor anywhere else — and nothing renders a "feed_results" panel,
    # so it asserted against something that was never wired up. Removed rather than
    # writing the function purely to satisfy the test.
