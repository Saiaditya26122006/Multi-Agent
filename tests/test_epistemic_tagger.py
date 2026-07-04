"""Tests for the epistemic tagger service."""

import pytest

from services.epistemic_tagger import (
    tag_from_language,
    tag_batch,
    enforce_prefix,
)


class TestTagFromLanguage:
    def test_confirmed_cues(self):
        result = tag_from_language("We confirmed pricing with the dean")
        assert result["epistemic_status"] == "CONFIRMED"
        assert result["confidence"] >= 0.8

    def test_verified(self):
        result = tag_from_language("The data has been verified by the team")
        assert result["epistemic_status"] == "CONFIRMED"

    def test_assumption_cues(self):
        result = tag_from_language("I think the pricing should be per-department")
        assert result["epistemic_status"] == "ASSUMPTION"
        assert result["confidence"] >= 0.8

    def test_hypothesis(self):
        result = tag_from_language("Our hypothesis is that institutions prefer annual")
        assert result["epistemic_status"] == "ASSUMPTION"

    def test_maybe(self):
        result = tag_from_language("Maybe the buyer is the department head")
        assert result["epistemic_status"] == "ASSUMPTION"

    def test_contradiction_cues(self):
        result = tag_from_language("This contradicts what we said about pricing")
        assert result["epistemic_status"] == "CONTRADICTION"
        assert result["confidence"] >= 0.9

    def test_no_cues_returns_inferred(self):
        result = tag_from_language("Annual SaaS subscription model")
        assert result["epistemic_status"] == "INFERRED"
        assert result["confidence"] == 0.5
        assert result["cues_found"] == []

    def test_contradiction_beats_confirmed(self):
        result = tag_from_language("This contradicts verified data from last week")
        assert result["epistemic_status"] == "CONTRADICTION"


class TestTagBatch:
    def test_tags_multiple_facts(self):
        facts = [
            "I think pricing is per-seat",
            "We confirmed the buyer is the dean",
            "Annual subscription model",
        ]
        results = tag_batch(facts)
        assert len(results) == 3
        assert results[0]["epistemic_status"] == "ASSUMPTION"
        assert results[1]["epistemic_status"] == "CONFIRMED"
        assert results[2]["epistemic_status"] == "INFERRED"

    def test_each_result_has_fact(self):
        facts = ["Fact one", "Fact two"]
        results = tag_batch(facts)
        assert results[0]["fact"] == "Fact one"
        assert results[1]["fact"] == "Fact two"


class TestEnforcePrefix:
    def test_adds_prefix(self):
        result = enforce_prefix("Annual SaaS pricing", "ASSUMPTION")
        assert result == "[ASSUMPTION] Annual SaaS pricing"

    def test_confirmed_prefix(self):
        result = enforce_prefix("Dean is the buyer", "CONFIRMED")
        assert result == "[CONFIRMED] Dean is the buyer"

    def test_does_not_double_prefix(self):
        result = enforce_prefix("[ASSUMPTION] Already prefixed", "CONFIRMED")
        assert result == "[ASSUMPTION] Already prefixed"

    def test_inferred_prefix(self):
        result = enforce_prefix("Some neutral fact", "INFERRED")
        assert result == "[INFERRED] Some neutral fact"
