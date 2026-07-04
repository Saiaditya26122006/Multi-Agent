"""Tests for the node indexer service."""

import pytest
from unittest.mock import patch, MagicMock

from services.node_indexer import (
    build_augmented_text,
    get_section_for_node,
    get_all_sections,
    get_section_descriptions,
    retrieve_candidate_nodes,
    verify_retrieval_quality,
)


class TestBuildAugmentedText:
    def test_combines_fields(self):
        node = {
            "node_id": "BP.1.1.3",
            "node_title": "Core Product Object",
            "purpose": "Define the product object",
            "required_output": "Product definition statement",
            "prohibited_claims": "Must not claim demand",
        }
        text = build_augmented_text(node)
        assert "BP.1.1.3" in text
        assert "Core Product Object" in text
        assert "Define the product object" in text
        assert "requires:" in text
        assert "NOT:" in text

    def test_handles_missing_fields(self):
        node = {"node_id": "BP.1", "node_title": "Product"}
        text = build_augmented_text(node)
        assert "BP.1" in text
        assert "Product" in text

    def test_purpose_same_as_title_not_duplicated(self):
        node = {"node_id": "BP.1.1", "node_title": "Product Definition", "purpose": "Product Definition"}
        text = build_augmented_text(node)
        assert text.count("Product Definition") == 1


class TestGetSectionForNode:
    def test_extracts_section_number(self):
        assert get_section_for_node("BP.1.1.3") == "1"
        assert get_section_for_node("BP.9.2.1") == "9"
        assert get_section_for_node("BP.13.1") == "13"

    def test_top_level(self):
        assert get_section_for_node("BP.1") == "1"

    def test_invalid(self):
        assert get_section_for_node("invalid") is None


class TestGetAllSections:
    def test_returns_sections(self):
        sections = get_all_sections()
        assert len(sections) >= 1
        assert all("section_id" in s for s in sections)
        assert all("title" in s for s in sections)


class TestGetSectionDescriptions:
    def test_returns_dict(self):
        descriptions = get_section_descriptions()
        assert isinstance(descriptions, dict)
        assert len(descriptions) >= 1
        for key, value in descriptions.items():
            assert key.startswith("BP.")
            assert isinstance(value, str)


class TestRetrieveCandidateNodes:
    @patch("services.rag_service.retrieve")
    def test_returns_candidates(self, mock_retrieve):
        mock_chunk = MagicMock()
        mock_chunk.metadata = {
            "node_id": "BP.1.1.3",
            "node_title": "Core Product",
            "purpose": "Define product",
            "required_output": "Product definition",
            "prohibited_claims": "Must not claim demand",
        }
        mock_chunk.similarity = 0.75
        mock_chunk.content = "BP.1.1.3 | Core Product"
        mock_retrieve.return_value = [mock_chunk]

        candidates = retrieve_candidate_nodes("EpistemicOS manuscript tool")
        assert len(candidates) == 1
        assert candidates[0]["node_id"] == "BP.1.1.3"
        assert candidates[0]["similarity"] == 0.75
        assert "demand" in candidates[0]["prohibited_claims"]

    @patch("services.rag_service.retrieve", return_value=[])
    def test_empty_results(self, mock_retrieve):
        candidates = retrieve_candidate_nodes("Irrelevant query")
        assert candidates == []


class TestVerifyRetrievalQuality:
    @patch("services.node_indexer.retrieve_candidate_nodes")
    def test_calculates_hit_rate(self, mock_retrieve):
        mock_retrieve.side_effect = [
            [{"node_id": "BP.1.1.3"}, {"node_id": "BP.1.1.4"}],
            [{"node_id": "BP.9.1.4"}, {"node_id": "BP.9.1.5"}],
            [{"node_id": "BP.5.1.1"}],
        ]

        test_facts = [
            {"fact": "Product is a diagnostics tool", "expected_node_id": "BP.1.1.3"},
            {"fact": "Revenue from SaaS", "expected_node_id": "BP.9.1.4"},
            {"fact": "Buyer is the dean", "expected_node_id": "BP.5.1.2"},
        ]

        result = verify_retrieval_quality(test_facts)
        assert result["total"] == 3
        assert result["hits"] == 2
        assert result["misses"] == 1
        assert result["hit_rate"] == 66.7

    @patch("services.node_indexer.retrieve_candidate_nodes", return_value=[])
    def test_all_misses(self, mock_retrieve):
        test_facts = [
            {"fact": "Test", "expected_node_id": "BP.1.1.1"},
        ]
        result = verify_retrieval_quality(test_facts)
        assert result["hits"] == 0
        assert result["hit_rate"] == 0.0
