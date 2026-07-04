"""Tests for the non-scope router service."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from services.non_scope_router import (
    route_to_non_scope,
    get_non_scope_queue,
    get_non_scope_count,
    resolve_non_scope,
    NON_SCOPE_FILE,
)


@pytest.fixture(autouse=True)
def clean_non_scope_file(tmp_path):
    """Use a temporary non-scope file for tests."""
    test_file = tmp_path / "non_scope.json"
    with patch("services.non_scope_router.NON_SCOPE_FILE", test_file):
        yield test_file


class TestRouteToNonScope:
    def test_stores_fact(self, clean_non_scope_file):
        with patch("services.non_scope_router.NON_SCOPE_FILE", clean_non_scope_file):
            item_id = route_to_non_scope(
                fact="This doesn't fit anywhere",
                reason="No node match above threshold",
                confidence=0.3,
            )
            assert item_id.startswith("ns_")

            queue = get_non_scope_queue()
            assert len(queue) == 1
            assert queue[0]["fact"] == "This doesn't fit anywhere"
            assert queue[0]["reason"] == "No node match above threshold"

    def test_multiple_items(self, clean_non_scope_file):
        with patch("services.non_scope_router.NON_SCOPE_FILE", clean_non_scope_file):
            route_to_non_scope(fact="Fact one", reason="Reason one")
            route_to_non_scope(fact="Fact two", reason="Reason two")

            assert get_non_scope_count() == 2


class TestGetNonScopeQueue:
    def test_empty_queue(self, clean_non_scope_file):
        with patch("services.non_scope_router.NON_SCOPE_FILE", clean_non_scope_file):
            queue = get_non_scope_queue()
            assert queue == []


class TestResolveNonScope:
    def test_resolve_map_to_node(self, clean_non_scope_file):
        with patch("services.non_scope_router.NON_SCOPE_FILE", clean_non_scope_file):
            with patch("services.non_scope_router._store_resolved_mapping"):
                item_id = route_to_non_scope(fact="Pricing data", reason="Low confidence")

                result = resolve_non_scope(
                    item_id=item_id,
                    action="map_to_node",
                    target_node="BP.9.1.4",
                    reason="Manual review confirms fit",
                )
                assert result["success"] is True
                assert result["action"] == "map_to_node"

                assert get_non_scope_count() == 0

    def test_resolve_discard(self, clean_non_scope_file):
        with patch("services.non_scope_router.NON_SCOPE_FILE", clean_non_scope_file):
            item_id = route_to_non_scope(fact="Irrelevant noise", reason="No match")

            result = resolve_non_scope(
                item_id=item_id,
                action="discard",
                reason="Not relevant to business plan",
            )
            assert result["success"] is True

            assert get_non_scope_count() == 0

    def test_resolve_invalid_id(self, clean_non_scope_file):
        with patch("services.non_scope_router.NON_SCOPE_FILE", clean_non_scope_file):
            result = resolve_non_scope(
                item_id="ns_9999",
                action="discard",
            )
            assert result["success"] is False
            assert "not found" in result["error"]
