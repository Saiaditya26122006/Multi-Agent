"""Tests for the precision mapper agent."""

import pytest
from unittest.mock import patch, MagicMock

from agents.phase2.precision_mapper import (
    classify_section,
    map_fact_to_node,
    map_batch,
    store_mapping,
    _extract_signal,
    _check_boundaries,
    _rewrite_signal,
    _generate_rationale,
    _compute_confidence,
    _non_scope_result,
    CONFIDENCE_THRESHOLDS,
)


class TestClassifySection:
    @patch("services.node_indexer.retrieve_candidate_nodes")
    def test_classifies_to_section(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"node_id": "BP.1.1.3", "node_title": "Product Definition", "similarity": 0.72}
        ]
        result = classify_section("EpistemicOS is a manuscript diagnostics tool")
        assert result["section_id"] == "BP.1"
        assert result["section_num"] == "1"
        assert result["confidence"] == 0.72

    @patch("services.node_indexer.retrieve_candidate_nodes", return_value=[])
    def test_no_candidates(self, mock_retrieve):
        result = classify_section("Completely unrelated text")
        assert result["section_id"] is None
        assert result["confidence"] == 0.0


class TestMapFactToNode:
    @patch("services.node_indexer.retrieve_candidate_nodes")
    def test_maps_with_high_confidence(self, mock_retrieve):
        mock_retrieve.return_value = [
            {
                "node_id": "BP.1.1.3",
                "node_title": "Core Product Object",
                "purpose": "Define the product object",
                "required_output": "Product definition",
                "prohibited_claims": "",
                "similarity": 0.8,
                "content": "BP.1.1.3 | Core Product Object",
            },
            {
                "node_id": "BP.1.1.4",
                "node_title": "User Function",
                "purpose": "Define user function",
                "required_output": "Function definition",
                "prohibited_claims": "",
                "similarity": 0.55,
                "content": "BP.1.1.4 | User Function",
            },
        ]
        result = map_fact_to_node(
            fact="EpistemicOS analyzes manuscript epistemic structure",
            epistemic_status="CONFIRMED",
            section_hint="1",
        )
        assert result["node_id"] == "BP.1.1.3"
        assert result["is_non_scope"] is False
        assert result["confidence"] > 0.7
        assert "[CONFIRMED]" in result["extracted_signal"]
        assert len(result["secondary_nodes"]) == 1

    @patch("services.node_indexer.retrieve_candidate_nodes", return_value=[])
    def test_no_candidates_returns_non_scope(self, mock_retrieve):
        result = map_fact_to_node(fact="Random noise text")
        assert result["is_non_scope"] is True
        assert result["node_id"] is None

    @patch("services.node_indexer.retrieve_candidate_nodes")
    def test_low_similarity_returns_non_scope(self, mock_retrieve):
        mock_retrieve.return_value = [
            {
                "node_id": "BP.1.1.1",
                "node_title": "Something",
                "purpose": "",
                "required_output": "",
                "prohibited_claims": "",
                "similarity": 0.3,
                "content": "BP.1.1.1",
            },
        ]
        result = map_fact_to_node(fact="Weak match text")
        assert result["is_non_scope"] is True


class TestMapBatch:
    @patch("agents.phase2.precision_mapper.map_fact_to_node")
    def test_maps_all_facts(self, mock_map):
        mock_map.return_value = {
            "node_id": "BP.1.1.1",
            "is_non_scope": False,
            "confidence": 0.8,
        }
        facts = [
            {"text": "Fact one", "epistemic_status": "CONFIRMED"},
            {"text": "Fact two", "epistemic_status": "ASSUMPTION"},
        ]
        results = map_batch(facts, section_hint="1")
        assert len(results) == 2
        assert mock_map.call_count == 2


class TestStoreMappingResult:
    @patch("services.rag_service.store")
    def test_stores_valid_mapping(self, mock_store):
        from services.rag_service import StoreOutcome, StoreResult

        mock_store.return_value = StoreResult(StoreOutcome.STORED, id="chunk_123")
        mapping = {
            "node_id": "BP.1.1.3",
            "node_title": "Core Product",
            "extracted_signal": "[CONFIRMED] EpistemicOS is a diagnostics tool",
            "rationale": "Fits product definition",
            "confidence": 0.8,
            "boundary_violations": [],
            "is_non_scope": False,
            "primary_secondary": "primary",
            "secondary_nodes": [],
            "epistemic_status": "CONFIRMED",
            "section": "1",
        }
        result = store_mapping(mapping, session_id="test_session")
        assert result == "chunk_123"
        mock_store.assert_called_once()

    @patch("services.non_scope_router.route_to_non_scope", return_value="ns_0001")
    def test_routes_non_scope_to_queue(self, mock_route):
        mapping = {
            "is_non_scope": True,
            "non_scope_reason": "Below threshold",
            "extracted_signal": "Some text",
            "confidence": 0.3,
        }
        result = store_mapping(mapping, session_id="test_session")
        assert result is None
        mock_route.assert_called_once()


class TestExtractSignal:
    def test_short_fact_unchanged(self):
        result = _extract_signal("Short fact", {"node_id": "BP.1.1.1"})
        assert result == "Short fact"

    def test_long_fact_truncated(self):
        long_fact = "A" * 250
        result = _extract_signal(long_fact, {"node_id": "BP.1.1.1"})
        assert len(result) <= 200


class TestCheckBoundaries:
    def test_no_violations_when_clean(self):
        node = {"prohibited_claims": "Must not claim demand or adoption"}
        signal = "Annual SaaS subscription model"
        violations = _check_boundaries(signal, node)
        assert violations == []

    def test_detects_demand_violation(self):
        node = {"prohibited_claims": "Must not claim demand or adoption"}
        signal = "Market demand exists for this product"
        violations = _check_boundaries(signal, node)
        assert len(violations) > 0
        assert any("demand" in v.lower() for v in violations)

    def test_detects_buyer_willingness(self):
        node = {"prohibited_claims": "Must not claim buyer willingness to pay or pricing adequacy"}
        signal = "Buyers will pay $10k annually for this product"
        violations = _check_boundaries(signal, node)
        assert len(violations) > 0

    def test_no_prohibited_claims(self):
        node = {"prohibited_claims": ""}
        signal = "Anything goes here"
        violations = _check_boundaries(signal, node)
        assert violations == []


class TestRewriteSignal:
    def test_adds_demand_qualifier(self):
        signal = "Revenue from market demand"
        violations = ["Signal implies 'demand' which is prohibited"]
        result = _rewrite_signal(signal, violations)
        assert "(does not imply market demand)" in result

    def test_no_violations_unchanged(self):
        signal = "Clean signal"
        result = _rewrite_signal(signal, [])
        assert result == "Clean signal"


class TestComputeConfidence:
    def test_no_violations_keeps_similarity(self):
        assert _compute_confidence(0.8, []) == 0.8

    def test_violations_reduce_confidence(self):
        result = _compute_confidence(0.8, ["violation 1", "violation 2"])
        assert result == 0.6

    def test_floor_at_zero(self):
        result = _compute_confidence(0.2, ["v1", "v2", "v3", "v4"])
        assert result == 0.0


class TestNonScopeResult:
    def test_structure(self):
        result = _non_scope_result("Some fact", "No match")
        assert result["is_non_scope"] is True
        assert result["node_id"] is None
        assert result["non_scope_reason"] == "No match"
        assert result["confidence"] == 0.0
