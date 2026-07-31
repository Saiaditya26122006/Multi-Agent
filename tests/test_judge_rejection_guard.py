"""The judge may not rank a node its own note rejects.

Five cards shipped with decision=ready while every note read "A POOR FIT since
these are external competitor tools, not internal capabilities". The note argued
against the node and the node was proposed anyway.

These tests are deterministic — they exercise the parse and gate directly, with
no Bedrock call — because the guarantee has to hold on whatever the model
returns, including the shapes it returns rarely.
"""

import numpy as np
import pytest

from services.feed_classifier_v3 import (
    Architecture,
    _note_rejects,
    _rank_from_parsed,
)
from services.feed_pipeline import (
    NO_PROPOSAL,
    PROPOSED,
    ReviewCard,
    _apply_proposal,
    bucket_of,
)
from services.feed_classifier_v3 import Proposal


@pytest.fixture
def arch() -> Architecture:
    """Two real-looking nodes, enough for the parse to resolve ids."""
    nodes = {
        "BP.8.1.3": {
            "node_id": "BP.8.1.3",
            "parent_node": "BP.8.1",
            "node_title": "Direct competitors",
            "degraded_target": False,
            "degraded_reason": None,
        },
        "BP.1.1.3": {
            "node_id": "BP.1.1.3",
            "parent_node": "BP.1.1",
            "node_title": "Diagnostic capability map",
            "degraded_target": False,
            "degraded_reason": None,
        },
    }
    return Architecture(
        leaf_ids=list(nodes),
        leaf_matrix=np.zeros((2, 4), dtype=np.float32),
        nodes=nodes,
        siblings={},
    )


def _candidates(ranked):
    return [c.node_id for c in ranked]


class TestFitVerdict:
    """The judge's explicit poor/fits verdict."""

    def test_poor_candidate_is_dropped(self, arch):
        parsed = {
            "candidates": [
                {
                    "note": "External tools, not ours.",
                    "fit": "poor",
                    "node_id": "BP.1.1.3",
                },
                {
                    "note": "Names the competing tools.",
                    "fit": "fits",
                    "node_id": "BP.8.1.3",
                },
            ]
        }
        assert _candidates(_rank_from_parsed(parsed, arch, 5)) == ["BP.8.1.3"]

    def test_all_poor_leaves_an_empty_shortlist(self, arch):
        parsed = {
            "candidates": [
                {"note": "Wrong subject.", "fit": "poor", "node_id": "BP.1.1.3"},
                {"note": "Also wrong.", "fit": "poor", "node_id": "BP.8.1.3"},
            ]
        }
        assert _rank_from_parsed(parsed, arch, 5) == []

    def test_surviving_candidates_are_renumbered_contiguously(self, arch):
        parsed = {
            "candidates": [
                {"note": "No.", "fit": "poor", "node_id": "BP.1.1.3"},
                {"note": "Yes.", "fit": "fits", "node_id": "BP.8.1.3"},
            ]
        }
        assert [c.rank for c in _rank_from_parsed(parsed, arch, 5)] == [1]

    def test_unrecognised_fit_falls_back_to_the_note(self, arch):
        """A stray verdict word must not empty an otherwise good shortlist."""
        parsed = {
            "candidates": [
                {"note": "Covers the claim.", "fit": "good", "node_id": "BP.8.1.3"},
            ]
        }
        assert _candidates(_rank_from_parsed(parsed, arch, 5)) == ["BP.8.1.3"]

    def test_absent_fit_keeps_a_supporting_note(self, arch):
        parsed = {"candidates": [{"note": "Covers the claim.", "node_id": "BP.8.1.3"}]}
        assert _candidates(_rank_from_parsed(parsed, arch, 5)) == ["BP.8.1.3"]


class TestNoteBackstop:
    """The note is read even when `fit` says the candidate is fine."""

    def test_the_reported_note_is_caught(self, arch):
        """The exact note from the five bad cards, labelled fits."""
        parsed = {
            "candidates": [
                {
                    "note": (
                        "Filing here would treat the four depth-engine tools as part "
                        "of EpistemicOS's own diagnostic capability map — a poor fit "
                        "since these are external competitor tools, not internal "
                        "capabilities."
                    ),
                    "fit": "fits",
                    "node_id": "BP.1.1.3",
                }
            ]
        }
        assert _rank_from_parsed(parsed, arch, 5) == []

    @pytest.mark.parametrize(
        "note",
        [
            "This is a poor fit for the fact.",
            "The fact does not belong at this node.",
            "Filing here would misrepresent the claim.",
            "Wrong section — this is about pricing.",
            "The node does not cover external tooling.",
        ],
    )
    def test_rejecting_phrasings(self, note, arch):
        parsed = {"candidates": [{"note": note, "fit": "fits", "node_id": "BP.8.1.3"}]}
        assert _rank_from_parsed(parsed, arch, 5) == []

    @pytest.mark.parametrize(
        "note",
        [
            "Files the claim as the buyer definition.",
            "This node's required output is the pricing table.",
            "Closest of the siblings; the others are about renewal.",
        ],
    )
    def test_supporting_notes_survive(self, note, arch):
        parsed = {"candidates": [{"note": note, "fit": "fits", "node_id": "BP.8.1.3"}]}
        assert _candidates(_rank_from_parsed(parsed, arch, 5)) == ["BP.8.1.3"]

    def test_detector_is_case_insensitive(self):
        assert _note_rejects("A POOR FIT since these are external tools")


class TestCardNeverReadsReady:
    """The end of the chain: an emptied shortlist must not surface as ready."""

    def test_no_proposal_card_is_nofit(self):
        card = ReviewCard(fact="The depth engine contains four tools.", index=0)
        _apply_proposal(
            card,
            Proposal(
                fact=card.fact,
                decision=NO_PROPOSAL,
                candidates=[],
                sections=[],
                section_margin=None,
                considered_leaf_ids=[],
                reason="every candidate's note rejected its own node",
            ),
        )
        assert bucket_of(card) == "nofit"
        assert card.proposed_node_id is None

    def test_a_real_shortlist_still_reads_ready(self, arch):
        """The guard must not turn every card into a no-match."""
        ranked = _rank_from_parsed(
            {
                "candidates": [
                    {
                        "note": "Names the competitors.",
                        "fit": "fits",
                        "node_id": "BP.8.1.3",
                    }
                ]
            },
            arch,
            5,
        )
        card = ReviewCard(fact="Iris.ai is a competitor.", index=0)
        _apply_proposal(
            card,
            Proposal(
                fact=card.fact,
                decision=PROPOSED,
                candidates=ranked,
                sections=[],
                section_margin=None,
                considered_leaf_ids=[],
                reason="competitor naming",
            ),
        )
        assert bucket_of(card) == "ready"
        assert card.proposed_node_id == "BP.8.1.3"
