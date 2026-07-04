"""Tests for services/dependency_checker.py."""

import json
from unittest.mock import mock_open, patch

import pytest

from services.dependency_checker import (
    get_blockers_for,
    get_cascade_risk,
    get_dependency_chain,
    get_dependency_graph,
    get_downstream_impact,
)

MOCK_DEPENDENCIES_DATA = {
    "_meta": {"source": "test", "last_updated": "2026-07-01"},
    "dependencies": {
        "BP.1.1.1": ["BP.1"],
        "BP.1.1.2": ["BP.1.1.1"],
        "BP.1.1.3": ["BP.1.1.1", "BP.1.1.2"],
        "BP.1.2.1": ["BP.1.1.1", "BP.1.1.3"],
        "BP.1.2.2": ["BP.1.1.3"],
        "BP.2.1": ["BP.1.1.3", "BP.1.2.1"],
        "BP.2.2": ["BP.2.1"],
        "BP.3.1": ["BP.2.1", "BP.2.2"],
    },
    "reopen_triggers": {},
}


def _mock_open_deps():
    """Return a mock that simulates reading bp_dependencies.json."""
    return mock_open(read_data=json.dumps(MOCK_DEPENDENCIES_DATA))


@patch("builtins.open", _mock_open_deps())
def test_get_dependency_graph_returns_full_dict():
    """get_dependency_graph should return the dependencies dict."""
    graph = get_dependency_graph()
    assert isinstance(graph, dict)
    assert "BP.1.1.1" in graph
    assert graph["BP.1.1.1"] == ["BP.1"]
    assert len(graph) == 8


@patch("builtins.open", side_effect=FileNotFoundError("not found"))
def test_get_dependency_graph_missing_file(mock_file):
    """get_dependency_graph should return empty dict when file is missing."""
    graph = get_dependency_graph()
    assert graph == {}


@patch("builtins.open", _mock_open_deps())
def test_get_blockers_for_section_with_external_deps():
    """get_blockers_for should return external dependencies for a section."""
    blockers = get_blockers_for("BP.1.2")
    # BP.1.2.1 depends on BP.1.1.1 and BP.1.1.3
    # BP.1.2.2 depends on BP.1.1.3
    # These are all outside BP.1.2
    assert "BP.1.1.1" in blockers
    assert "BP.1.1.3" in blockers


@patch("builtins.open", _mock_open_deps())
def test_get_blockers_for_section_without_external_deps():
    """Nodes depending only on siblings should return no blockers."""
    # BP.1.1.2 depends on BP.1.1.1 (same section)
    # BP.1.1.3 depends on BP.1.1.1, BP.1.1.2 (same section)
    # BP.1.1.1 depends on BP.1 which is the section itself
    blockers = get_blockers_for("BP.1.1")
    # BP.1 is outside BP.1.1 (BP.1 != BP.1.1 and doesn't start with "BP.1.1.")
    assert "BP.1" in blockers
    # But no other external deps
    assert all(
        b == "BP.1" or not b.startswith("BP.1.1")
        for b in blockers
    )


@patch("builtins.open", _mock_open_deps())
def test_get_downstream_impact():
    """get_downstream_impact should find nodes outside section that depend on it."""
    # BP.1.1 section contains BP.1.1.1, BP.1.1.2, BP.1.1.3
    # BP.1.2.1 depends on BP.1.1.1 and BP.1.1.3 (outside BP.1.1)
    # BP.1.2.2 depends on BP.1.1.3 (outside BP.1.1)
    # BP.2.1 depends on BP.1.1.3 (outside BP.1.1)
    impact = get_downstream_impact("BP.1.1")
    assert "BP.1.2.1" in impact
    assert "BP.1.2.2" in impact
    assert "BP.2.1" in impact


@patch("builtins.open", _mock_open_deps())
def test_get_cascade_risk_leaf_node():
    """A leaf node with no dependents should have cascade risk 0."""
    risk = get_cascade_risk("BP.3.1")
    assert risk == 0


@patch("builtins.open", _mock_open_deps())
def test_get_cascade_risk_root_node():
    """A root-like node should have high cascade risk."""
    # BP.1.1.1 -> BP.1.1.2, BP.1.1.3, BP.1.2.1 directly
    # BP.1.1.3 -> BP.1.2.1, BP.1.2.2, BP.2.1
    # BP.2.1 -> BP.2.2, BP.3.1
    # Transitive from BP.1.1.1:
    #   BP.1.1.2, BP.1.1.3, BP.1.2.1, BP.1.2.2, BP.2.1, BP.2.2, BP.3.1 = 7
    risk = get_cascade_risk("BP.1.1.1")
    assert risk == 7


@patch("builtins.open", _mock_open_deps())
def test_get_dependency_chain():
    """get_dependency_chain should return structured chain for a section."""
    chain = get_dependency_chain("BP.2")
    assert chain["section_id"] == "BP.2"
    # BP.2.1 depends on BP.1.1.3 and BP.1.2.1 (upstream/external)
    assert "BP.1.1.3" in chain["upstream"]
    assert "BP.1.2.1" in chain["upstream"]
    # BP.3.1 depends on BP.2.1, BP.2.2 (downstream)
    assert "BP.3.1" in chain["downstream"]
    # blocked_by: upstream nodes that themselves have deps
    # BP.1.1.3 has deps [BP.1.1.1, BP.1.1.2] -> blocked
    # BP.1.2.1 has deps [BP.1.1.1, BP.1.1.3] -> blocked
    assert "BP.1.1.3" in chain["blocked_by"]
    assert "BP.1.2.1" in chain["blocked_by"]
