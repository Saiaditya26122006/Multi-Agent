"""Passages are stored verbatim and attach only where a span earns it.

Two guarantees, tested where they can be broken:

* ``source[start:end] == text`` for every passage that reaches storage, and the
  write path refuses anything else.
* An attachment exists only when the judge quoted a real span of the passage.
  A node proposed on general relatedness produces no span, so it is dropped.

Deterministic — no Bedrock call. The judge's actual reading of a passage is
measured live; what is tested here is that the code checks rather than trusts.
"""

import numpy as np
import pytest

from services.feed_classifier_v3 import Architecture
from services.passage_chunker import (
    MIN_PASSAGE_CHARS,
    Passage,
    _anchor_cuts,
    _passages_from_cuts,
    audit_passages,
    split_passages_structural,
)
from services.passage_classifier import (
    LEVEL_LEAF,
    LEVEL_SECTION,
    Attachment,
    _attachments_from,
    _locate_span,
    highlight,
)
from services.passage_pipeline import (
    PassageCard,
    confirm_passage,
    verify_passage_spans,
)

CLAIM_1 = (
    'Claim 1 — "We assess the argument, not the apparatus." The demonstrable '
    "market failure is that volume != coverage: the highest-output tool scored "
    "zero. EpistemicOS should lead with reviewer-risk coverage and explicitly "
    "refuse to compete on reference-and-typo count. This is the single strongest "
    "exhibit in the benchmark."
)


@pytest.fixture
def arch() -> Architecture:
    nodes = {
        "BP.8.1": {"node_id": "BP.8.1", "node_title": "Competitive Landscape"},
        "BP.8.1.4": {
            "node_id": "BP.8.1.4",
            "node_title": "Feature Comparison Framework",
            "degraded_target": False,
            "degraded_reason": None,
        },
        "BP.8.3.3": {
            "node_id": "BP.8.3.3",
            "node_title": "Positioning Decision",
            "degraded_target": False,
            "degraded_reason": None,
        },
        "BP.1.1.3": {
            "node_id": "BP.1.1.3",
            "node_title": "Core Diagnostic Function",
            "degraded_target": False,
            "degraded_reason": None,
        },
    }
    return Architecture(
        leaf_ids=["BP.8.1.4", "BP.8.3.3", "BP.1.1.3"],
        leaf_matrix=np.zeros((3, 4), dtype=np.float32),
        nodes=nodes,
        siblings={"BP.8.1": ["BP.8.1.4"]},
    )


class TestStructuralSplitting:
    """The deterministic fallback. No LLM — these must not make a Bedrock call."""

    def test_claim_1_block_is_one_passage(self):
        passages = split_passages_structural(CLAIM_1)
        assert len(passages) == 1
        assert passages[0].text == CLAIM_1

    def test_every_passage_is_a_slice_of_the_source(self):
        doc = "First paragraph, long enough to stand alone as a unit.\n\n" + CLAIM_1
        for p in split_passages_structural(doc):
            assert doc[p.start_char : p.end_char] == p.text

    def test_blank_line_starts_a_new_passage(self):
        doc = "A" * 60 + "\n\n" + "B" * 60
        passages = split_passages_structural(doc)
        assert len(passages) == 2
        assert passages[0].text == "A" * 60

    def test_a_label_starts_a_new_passage_without_a_blank_line(self):
        doc = (
            "Some opening prose that runs on for a while here.\n"
            "Claim 2 — the second one, which is also long."
        )
        passages = split_passages_structural(doc)
        assert len(passages) == 2
        assert passages[1].text.startswith("Claim 2")

    def test_label_is_captured(self):
        assert split_passages_structural(CLAIM_1)[0].label == "Claim 1"

    def test_unlabelled_passage_has_no_label(self):
        doc = "Just an ordinary paragraph with no label on the front of it at all."
        assert split_passages_structural(doc)[0].label is None


class TestAnchorCuts:
    """The LLM path's boundary derivation, without the LLM.

    The model returns openings; these tests cover what the code does with them,
    which is where text could be lost, duplicated or reordered.
    """

    DOC = "Alpha one two three. Beta four five six. Gamma seven eight nine."

    def test_openings_become_ordered_cuts(self):
        items = [
            {"opening": "Alpha one two", "label": "a"},
            {"opening": "Beta four five", "label": "b"},
        ]
        beta = self.DOC.index("Beta")
        assert _anchor_cuts(self.DOC, items) == [(0, "a"), (beta, "b")]

    def test_first_cut_is_forced_to_zero(self):
        """Text before the model's first opening must not be orphaned."""
        items = [{"opening": "Beta four five", "label": "b"}]
        cuts = _anchor_cuts(self.DOC, items)
        assert cuts[0][0] == 0

    def test_an_unfindable_opening_is_dropped(self):
        items = [
            {"opening": "Alpha one two", "label": "a"},
            {"opening": "text that is not in the document", "label": "ghost"},
            {"opening": "Gamma seven eight", "label": "c"},
        ]
        assert [c[1] for c in _anchor_cuts(self.DOC, items)] == ["a", "c"]

    def test_a_backwards_opening_is_dropped(self):
        """Out-of-order openings would reorder the document."""
        items = [
            {"opening": "Gamma seven eight", "label": "c"},
            {"opening": "Alpha one two", "label": "a"},
        ]
        cuts = _anchor_cuts(self.DOC, items)
        assert [c[0] for c in cuts] == sorted(c[0] for c in cuts)

    def test_whitespace_normalised_opening_is_recovered(self):
        doc = "Alpha one\ntwo three. Beta four five six."
        items = [{"opening": "Alpha one two three", "label": "a"}]
        assert _anchor_cuts(doc, items)[0][0] == 0

    def test_garbage_elements_are_skipped(self):
        items = ["nonsense", {"label": "no opening"}, {"opening": "Beta four five"}]
        assert len(_anchor_cuts(self.DOC, items)) >= 1

    def test_no_usable_openings_returns_nothing(self):
        assert _anchor_cuts(self.DOC, [{"opening": "absent"}]) == []

    def test_a_cut_snaps_back_over_a_list_marker(self):
        """A bullet belongs to the item it marks, whichever side the model cut."""
        doc = "Intro sentence here.\n- Parse the manuscript.\n- Discard the rest."
        with_marker = _anchor_cuts(
            doc, [{"opening": "Intro sentence"}, {"opening": "- Parse the manuscript"}]
        )
        without_marker = _anchor_cuts(
            doc, [{"opening": "Intro sentence"}, {"opening": "Parse the manuscript"}]
        )
        assert [c[0] for c in with_marker] == [c[0] for c in without_marker]
        assert doc[with_marker[1][0]] == "-"

    def test_snapping_never_crosses_content(self):
        doc = "Alpha one two three. Beta four five six."
        cuts = _anchor_cuts(doc, [{"opening": "Beta four five"}])
        assert doc[cuts[-1][0] :].startswith("Beta")


class TestCutsToPassages:
    """Cuts become contiguous verbatim slices — the guarantee, in code."""

    DOC = (
        "Alpha one two three four five six seven eight. "
        "Beta nine ten eleven twelve thirteen fourteen fifteen."
    )

    def test_passages_are_contiguous_and_verbatim(self):
        cut = self.DOC.index("Beta")
        passages = _passages_from_cuts(self.DOC, [(0, "a"), (cut, "b")])
        assert len(passages) == 2
        for p in passages:
            assert self.DOC[p.start_char : p.end_char] == p.text
            assert p.span_verified
        assert passages[0].end_char <= passages[1].start_char

    def test_no_character_is_lost(self):
        """Only separator whitespace may differ between input and output."""
        cut = self.DOC.index("Beta")
        passages = _passages_from_cuts(self.DOC, [(0, "a"), (cut, "b")])
        joined = "".join(p.text for p in passages)
        assert joined.replace(" ", "") == self.DOC.replace(" ", "")

    def test_llm_label_wins_over_the_structural_one(self):
        passages = _passages_from_cuts(self.DOC, [(0, "model label")])
        assert passages[0].label == "model label"

    def test_missing_label_falls_back_to_the_structural_one(self):
        doc = "Claim 1 — something that is long enough to be its own passage here."
        passages = _passages_from_cuts(doc, [(0, None)])
        assert passages[0].label == "Claim 1"

    def test_separators_are_not_stored(self):
        doc = "A" * 60 + "\n\n\n   " + "B" * 60
        for p in split_passages_structural(doc):
            assert p.text == p.text.strip()

    def test_short_block_folds_into_the_previous_one(self):
        doc = "A" * 60 + "\n\nshort"
        passages = split_passages_structural(doc)
        assert len(passages) == 1
        assert "short" in passages[0].text

    def test_empty_input(self):
        assert split_passages_structural("") == []
        assert split_passages_structural("   \n\n  ") == []

    def test_splitting_is_deterministic(self):
        doc = CLAIM_1 + "\n\n" + "B" * 80
        first = [(p.start_char, p.end_char) for p in split_passages_structural(doc)]
        assert all(
            [(p.start_char, p.end_char) for p in split_passages_structural(doc)]
            == first
            for _ in range(3)
        )


class TestVerbatimAudit:
    def test_sliced_passages_pass(self):
        passages = split_passages_structural(CLAIM_1)
        assert audit_passages(passages, CLAIM_1) == []
        assert all(p.span_verified for p in passages)

    def test_a_rewritten_passage_fails(self):
        p = Passage(text=CLAIM_1, start_char=0, end_char=len(CLAIM_1), index=0)
        p.text = p.text.replace("We assess", "EpistemicOS assesses")
        assert audit_passages([p], CLAIM_1) == [p]
        assert p.span_verified is False

    def test_card_spans_are_checked(self):
        card = PassageCard(text=CLAIM_1, index=0, start_char=0, end_char=len(CLAIM_1))
        assert verify_passage_spans([card], CLAIM_1) == []
        assert card.span_verified is True

    def test_confirm_refuses_an_unverified_card(self):
        card = PassageCard(
            text="not from the source", index=0, start_char=0, end_char=5
        )
        verify_passage_spans([card], CLAIM_1)
        with pytest.raises(ValueError, match="verbatim span"):
            confirm_passage(card, ["BP.8.1.4"], "alex", "run-1")

    def test_confirm_refuses_an_empty_node_list(self):
        card = PassageCard(text=CLAIM_1, index=0, start_char=0, end_char=len(CLAIM_1))
        verify_passage_spans([card], CLAIM_1)
        with pytest.raises(ValueError, match="no nodes"):
            confirm_passage(card, [], "alex", "run-1")


class TestSpanEarnsTheAttachment:
    def _item(self, node_id, span, fit="fits", reason="populates it"):
        return {"span": span, "reason": reason, "fit": fit, "node_id": node_id}

    def test_a_real_span_is_kept(self, arch):
        parsed = {
            "attachments": [
                self._item("BP.8.1.4", "the highest-output tool scored zero")
            ]
        }
        kept, dropped, overflow = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert [a.node_id for a in kept] == ["BP.8.1.4"]
        assert kept[0].span == "the highest-output tool scored zero"
        assert CLAIM_1[kept[0].span_start : kept[0].span_end] == kept[0].span

    def test_a_span_that_is_not_in_the_passage_is_dropped(self, arch):
        """The 'merely related' case: no real span to point at."""
        parsed = {
            "attachments": [
                self._item("BP.1.1.3", "the product's own diagnostic capability map")
            ]
        }
        kept, dropped, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert kept == []
        assert dropped[0]["why"] == "span is not in the passage"

    def test_an_empty_span_is_dropped(self, arch):
        parsed = {"attachments": [self._item("BP.8.1.4", "")]}
        kept, _, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert kept == []

    def test_a_paraphrased_span_is_dropped(self, arch):
        parsed = {
            "attachments": [
                self._item("BP.8.1.4", "the tool with the highest output scored zero")
            ]
        }
        kept, _, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert kept == []

    def test_whitespace_normalised_span_is_recovered(self, arch):
        text = "The tool\nscored zero on coverage."
        parsed = {"attachments": [self._item("BP.8.1.4", "The tool scored zero")]}
        kept, _, _ = _attachments_from(parsed, text, arch, 4)
        assert len(kept) == 1
        assert text[kept[0].span_start : kept[0].span_end] == kept[0].span

    def test_unknown_node_is_dropped(self, arch):
        parsed = {"attachments": [self._item("BP.99.9", "We assess the argument")]}
        kept, dropped, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert kept == []
        assert dropped[0]["why"] == "unknown node id"

    def test_poor_fit_is_dropped(self, arch):
        parsed = {
            "attachments": [
                self._item("BP.1.1.3", "We assess the argument", fit="poor")
            ]
        }
        assert _attachments_from(parsed, CLAIM_1, arch, 4)[0] == []

    def test_a_reason_that_rejects_its_node_is_dropped(self, arch):
        parsed = {
            "attachments": [
                self._item(
                    "BP.1.1.3",
                    "We assess the argument",
                    reason="a poor fit — this node covers internal capabilities",
                )
            ]
        }
        assert _attachments_from(parsed, CLAIM_1, arch, 4)[0] == []

    def test_duplicate_node_is_kept_once(self, arch):
        parsed = {
            "attachments": [
                self._item("BP.8.1.4", "We assess the argument"),
                self._item("BP.8.1.4", "the highest-output tool scored zero"),
            ]
        }
        kept, dropped, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert len(kept) == 1
        assert dropped[0]["why"] == "duplicate attachment"

    def test_same_span_may_earn_two_nodes(self, arch):
        span = "We assess the argument"
        parsed = {
            "attachments": [self._item("BP.8.1.4", span), self._item("BP.8.3.3", span)]
        }
        kept, _, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert [a.node_id for a in kept] == ["BP.8.1.4", "BP.8.3.3"]

    def test_cap_keeps_the_strongest_and_counts_the_rest(self, arch):
        span = "We assess the argument"
        parsed = {
            "attachments": [
                self._item("BP.8.1.4", span),
                self._item("BP.8.3.3", span),
                self._item("BP.1.1.3", span),
            ]
        }
        kept, _, overflow = _attachments_from(parsed, CLAIM_1, arch, 2)
        assert [a.node_id for a in kept] == ["BP.8.1.4", "BP.8.3.3"]
        assert overflow == 1

    def test_dropped_attachments_do_not_count_as_overflow(self, arch):
        parsed = {
            "attachments": [
                self._item("BP.8.1.4", "We assess the argument"),
                self._item("BP.1.1.3", "a span that is not present"),
            ]
        }
        kept, _, overflow = _attachments_from(parsed, CLAIM_1, arch, 1)
        assert len(kept) == 1
        assert overflow == 0

    def test_ranks_are_contiguous(self, arch):
        span = "We assess the argument"
        parsed = {
            "attachments": [
                self._item("BP.1.1.3", "not present at all"),
                self._item("BP.8.1.4", span),
                self._item("BP.8.3.3", span),
            ]
        }
        kept, _, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert [a.rank for a in kept] == [1, 2]

    def test_parent_section_is_levelled_as_a_section(self, arch):
        parsed = {"attachments": [self._item("BP.8.1", "We assess the argument")]}
        kept, _, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert kept[0].level == LEVEL_SECTION

    def test_leaf_is_levelled_as_a_leaf(self, arch):
        parsed = {"attachments": [self._item("BP.8.1.4", "We assess the argument")]}
        kept, _, _ = _attachments_from(parsed, CLAIM_1, arch, 4)
        assert kept[0].level == LEVEL_LEAF


class TestCandidatePool:
    """The parent fallback: a section must be a legal target, in BOTH pipelines."""

    def _section(self):
        from services.feed_classifier_v3 import SectionCandidate

        return SectionCandidate(
            section_id="BP.8.1",
            best_leaf_id="BP.8.1.4",
            best_leaf_similarity=0.2,
            leaves_in_induced_window=1,
        )

    def test_section_itself_is_offered_as_a_target(self, arch):
        from services.feed_classifier_v3 import candidate_pool

        ids = candidate_pool(arch, [self._section()])
        assert "BP.8.1.4" in ids and "BP.8.1" in ids

    def test_leaves_come_before_the_parent(self, arch):
        from services.feed_classifier_v3 import candidate_pool

        ids = candidate_pool(arch, [self._section()])
        assert ids.index("BP.8.1.4") < ids.index("BP.8.1")

    def test_is_section_distinguishes_parent_from_leaf(self, arch):
        from services.feed_classifier_v3 import is_section

        assert is_section(arch, "BP.8.1") is True
        assert is_section(arch, "BP.8.1.4") is False

    def test_the_fact_pipeline_ranks_a_section_as_section_level(self, arch):
        """A parent chosen by the fact judge is recorded as level=section."""
        from services.feed_classifier_v3 import _rank_from_parsed

        parsed = {
            "candidates": [
                {"note": "belongs in the section, no leaf covers it",
                 "fit": "fits", "node_id": "BP.8.1"}
            ]
        }
        ranked = _rank_from_parsed(parsed, arch, 5)
        assert [(c.node_id, c.level, c.section_id) for c in ranked] == [
            ("BP.8.1", "section", "BP.8.1")
        ]

    def test_the_fact_pipeline_ranks_a_leaf_as_leaf_level(self, arch):
        from services.feed_classifier_v3 import _rank_from_parsed

        parsed = {
            "candidates": [
                {"note": "covers it", "fit": "fits", "node_id": "BP.8.1.4"}
            ]
        }
        ranked = _rank_from_parsed(parsed, arch, 5)
        assert [(c.node_id, c.level, c.section_id) for c in ranked] == [
            ("BP.8.1.4", "leaf", "BP.8.1")
        ]


class TestLocateSpan:
    def test_exact(self):
        assert _locate_span("assess", "We assess it") == (3, 9)

    def test_absent(self):
        assert _locate_span("missing", "We assess it") == (-1, -1)

    def test_empty(self):
        assert _locate_span("", "We assess it") == (-1, -1)


class TestHighlight:
    def test_span_is_marked(self):
        a = Attachment(
            node_id="BP.8.1.4",
            title="t",
            level=LEVEL_LEAF,
            span="assess",
            span_start=3,
            span_end=9,
            reason="r",
            rank=1,
        )
        assert highlight("We assess it", a) == "We **assess** it"

    def test_out_of_range_span_returns_the_passage_unchanged(self):
        a = Attachment(
            node_id="BP.8.1.4",
            title="t",
            level=LEVEL_LEAF,
            span="x",
            span_start=50,
            span_end=60,
            reason="r",
            rank=1,
        )
        assert highlight("We assess it", a) == "We assess it"
