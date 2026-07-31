"""Stored facts are the author's words, not a rewrite of them.

The invariant, everywhere it can be broken::

    source[fact.start_char:fact.end_char] == fact.fact

Alex wrote "We assess the argument, not the apparatus." The system stored
"EpistemicOS assesses the argument, not the apparatus." These tests exist so that
cannot happen again without something going red.

Deterministic — the model is not called. What is tested is that the code slices
rather than trusts, and that the write path refuses anything unverified.
"""

import pytest

from services.feed_pipeline import (
    ReviewCard,
    _passage_for,
    confirm_card,
    verify_card_spans,
)
from services.semantic_chunker import (
    UNLOCATED,
    VERBATIM,
    Fact,
    audit_spans,
)

SOURCE = (
    'Claim 1 — "We assess the argument, not the apparatus." The demonstrable '
    "market failure is that volume != coverage: the highest-output tool scored "
    "zero. The new tier launched in March. It replaced the per-seat model."
)


def _fact_at(text: str, index: int = 0) -> Fact:
    """A fact built the way chunk_text builds one: sliced from the source."""
    start = SOURCE.index(text)
    end = start + len(text)
    return Fact(
        fact=SOURCE[start:end],
        source_quote=SOURCE[start:end],
        start_char=start,
        end_char=end,
        index=index,
    )


class TestSpanAudit:
    def test_a_sliced_fact_passes(self):
        f = _fact_at("We assess the argument, not the apparatus.")
        assert audit_spans([f], SOURCE) == []
        assert f.verdict == VERBATIM
        assert f.needs_review is False

    def test_a_rewritten_fact_fails(self):
        """The exact regression: a subject supplied that the source never had."""
        f = _fact_at("We assess the argument, not the apparatus.")
        f.fact = "EpistemicOS assesses the argument, not the apparatus."
        assert audit_spans([f], SOURCE) == [f]
        assert f.verdict == UNLOCATED
        assert f.needs_review is True

    def test_a_resolved_pronoun_fails(self):
        f = _fact_at("It replaced the per-seat model.")
        f.fact = "The new tier replaced the per-seat model."
        assert audit_spans([f], SOURCE) == [f]

    def test_an_unresolved_pronoun_passes(self):
        """The segment stays as written. This is the intended behaviour."""
        f = _fact_at("It replaced the per-seat model.")
        assert audit_spans([f], SOURCE) == []
        assert f.fact.startswith("It ")

    def test_an_unlocated_fact_is_flagged(self):
        f = Fact(
            fact="something the model invented",
            source_quote="",
            start_char=None,
            end_char=None,
            index=0,
        )
        assert audit_spans([f], SOURCE) == [f]
        assert f.verdict == UNLOCATED

    def test_one_character_of_drift_fails(self):
        f = _fact_at("The new tier launched in March.")
        f.fact = f.fact.replace("March", "march")
        assert audit_spans([f], SOURCE) == [f]


class TestCardSpans:
    def _card(self, text: str) -> ReviewCard:
        start = SOURCE.index(text)
        return ReviewCard(
            fact=SOURCE[start : start + len(text)],
            index=0,
            start_char=start,
            end_char=start + len(text),
        )

    def test_verified_card_is_marked(self):
        card = self._card("It replaced the per-seat model.")
        assert verify_card_spans([card], SOURCE) == []
        assert card.span_verified is True

    def test_rewritten_card_is_caught(self):
        card = self._card("It replaced the per-seat model.")
        card.fact = "The new tier replaced the per-seat model."
        assert verify_card_spans([card], SOURCE) == [card]
        assert card.span_verified is False

    def test_spanless_card_is_caught(self):
        card = ReviewCard(fact="invented", index=0)
        assert verify_card_spans([card], SOURCE) == [card]
        assert card.span_verified is False


class TestWritePathRefuses:
    def test_confirm_card_refuses_an_unverified_card(self):
        card = ReviewCard(fact="EpistemicOS assesses the argument.", index=0)
        with pytest.raises(ValueError, match="verbatim span"):
            confirm_card(card, "BP.1.1.3", "alex", "run-1")

    def test_confirm_card_refuses_a_span_that_stopped_matching(self):
        card = ReviewCard(
            fact="It replaced the per-seat model.", index=0, start_char=0, end_char=5
        )
        verify_card_spans([card], SOURCE)
        with pytest.raises(ValueError, match="verbatim span"):
            confirm_card(card, "BP.1.1.3", "alex", "run-1")


class TestPassage:
    """Comprehension context — read by the classifier, never stored."""

    def test_short_document_is_returned_whole(self):
        card = ReviewCard(fact="x", index=0, start_char=0, end_char=1)
        assert _passage_for([card], "short text") == "short text"

    def test_window_covers_the_referent(self):
        text = (
            "A" * 2000 + " The new tier launched in March. It replaced it. " + "B" * 2000
        )
        start = text.index("It replaced it.")
        card = ReviewCard(
            fact="It replaced it.",
            index=0,
            start_char=start,
            end_char=start + len("It replaced it."),
        )
        passage = _passage_for([card], text, window=100)
        assert "The new tier launched in March." in passage
        assert len(passage) < len(text)

    def test_group_spans_are_unioned(self):
        text = "one two three four five six seven eight nine ten"
        two = text.index("two")
        first = ReviewCard(fact="two", index=0, start_char=two, end_char=two + 3)
        last = ReviewCard(
            fact="nine",
            index=1,
            start_char=text.index("nine"),
            end_char=text.index("nine") + 4,
        )
        passage = _passage_for([first, last], text, window=0)
        assert passage == "two three four five six seven eight nine"

    def test_no_span_means_no_passage(self):
        assert _passage_for([ReviewCard(fact="x", index=0)], "some text") is None
