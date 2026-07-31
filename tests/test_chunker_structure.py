"""Roles and relationships survive the parse, and never invent an edge.

The chunker reads the whole passage in one call and already knows its argument
structure. These tests cover the part of carrying that structure out that must
hold regardless of what the model returns — id resolution, validation, and the
degradation path when a link cannot be determined. The model's actual reading of
a passage is measured live, not here.
"""

import pytest

from services.feed_pipeline import _redirect_collapsed_links
from services.semantic_chunker import (
    Fact,
    _parse_role,
    _resolve_relationships,
)


def _fact(index: int, text: str = "x") -> Fact:
    return Fact(
        fact=text, source_quote=text, start_char=None, end_char=None, index=index
    )


class TestRole:
    @pytest.mark.parametrize(
        "raw", ["claim", "evidence", "recommendation", "assessment", "definition"]
    )
    def test_valid_roles_pass(self, raw):
        assert _parse_role(raw, 0) == raw

    def test_case_and_space_are_normalised(self):
        assert _parse_role("  Evidence ", 0) == "evidence"

    def test_unknown_role_is_dropped(self):
        assert _parse_role("conclusion", 0) is None

    def test_absent_role_is_none(self):
        assert _parse_role(None, 0) is None


class TestRelationships:
    def test_ids_map_onto_fact_indices(self):
        facts = [_fact(0), _fact(1)]
        raw = {0: {"supported_by": [2]}, 1: {"supports": [1]}}
        _resolve_relationships(facts, raw, {1: 0, 2: 1})
        assert facts[0].relationships == {"supported_by": [1]}
        assert facts[1].relationships == {"supports": [0]}

    def test_link_to_an_unemitted_id_is_dropped(self):
        facts = [_fact(0)]
        _resolve_relationships(facts, {0: {"supports": [7]}}, {1: 0})
        assert facts[0].relationships == {}

    def test_self_reference_is_dropped(self):
        facts = [_fact(0)]
        _resolve_relationships(facts, {0: {"supports": [1]}}, {1: 0})
        assert facts[0].relationships == {}

    def test_unknown_relation_is_dropped(self):
        facts = [_fact(0), _fact(1)]
        _resolve_relationships(facts, {0: {"causes": [2]}}, {1: 0, 2: 1})
        assert facts[0].relationships == {}

    def test_duplicate_targets_are_collapsed(self):
        facts = [_fact(0), _fact(1)]
        _resolve_relationships(facts, {0: {"supports": [2, 2]}}, {1: 0, 2: 1})
        assert facts[0].relationships == {"supports": [1]}

    def test_no_relationships_object_leaves_an_empty_dict(self):
        facts = [_fact(0)]
        _resolve_relationships(facts, {0: None}, {1: 0})
        assert facts[0].relationships == {}

    def test_garbage_targets_do_not_raise(self):
        facts = [_fact(0), _fact(1)]
        _resolve_relationships(facts, {0: {"supports": [None, "x", 2]}}, {1: 0, 2: 1})
        assert facts[0].relationships == {"supports": [1]}


class TestLinksSurviveDedupe:
    """dedupe does not renumber, so links must follow a collapsed target."""

    def test_link_follows_the_survivor(self):
        kept, other = _fact(0), _fact(2)
        other.relationships = {"supports": [1]}
        _redirect_collapsed_links(
            [kept, other], [{"dropped_index": 1, "kept_index": 0}]
        )
        assert other.relationships == {"supports": [0]}

    def test_link_that_becomes_a_self_reference_is_dropped(self):
        kept = _fact(0)
        kept.relationships = {"supports": [1]}
        _redirect_collapsed_links([kept], [{"dropped_index": 1, "kept_index": 0}])
        assert kept.relationships == {}

    def test_target_that_survives_nowhere_is_dropped(self):
        """A chained collapse can leave a redirect pointing at a dead index."""
        fact = _fact(0)
        fact.relationships = {"supports": [1]}
        _redirect_collapsed_links(
            [fact], [{"dropped_index": 1, "kept_index": 2}]
        )
        assert fact.relationships == {}

    def test_no_drops_leaves_links_untouched(self):
        fact = _fact(0)
        fact.relationships = {"supports": [1]}
        _redirect_collapsed_links([fact, _fact(1)], [])
        assert fact.relationships == {"supports": [1]}
