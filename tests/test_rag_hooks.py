"""
Tests for services/rag_hooks.py

Requires live Supabase connection.
"""

import uuid
import pytest

from services.rag_hooks import (
    store_agent_insight,
    store_negative_knowledge,
    store_run_metadata,
    store_external_research,
    check_negative_knowledge,
)


class TestStoreAgentInsight:
    def test_store_agent_insight(self):
        chunk_id = store_agent_insight(
            agent_name="financial_modelling",
            insight=f"Break-even requires 47 clients {uuid.uuid4().hex[:6]}",
            section="12",
            run_id="run_test_001",
            confidence=0.7,
        )
        assert chunk_id is not None
        assert len(chunk_id) == 36


class TestStoreNegativeKnowledge:
    def test_store_negative_knowledge(self):
        chunk_id = store_negative_knowledge(
            what_failed=f"per-claim pricing {uuid.uuid4().hex[:6]}",
            reason="Alex said too granular",
            source="kill_decision",
        )
        assert chunk_id is not None


class TestStoreRunMetadata:
    def test_store_run_metadata(self):
        chunk_id = store_run_metadata(
            run_id=f"run_{uuid.uuid4().hex[:8]}",
            idea="EpistemicOS",
            sections_completed=["1", "3", "5", "8"],
            sections_failed={"12": "bedrock_timeout"},
            alex_verdict="adjust",
            alex_feedback="Section 8 too generic",
            total_tokens=142000,
            duration_seconds=340.5,
            quality_scores={"coherence": 7.2, "actionability": 6.1},
        )
        assert chunk_id is not None


class TestStoreExternalResearch:
    def test_store_external_research_has_freshness(self):
        chunk_id = store_external_research(
            query="Turnitin institutional clients count",
            results_summary=f"Turnitin has ~15000 clients {uuid.uuid4().hex[:6]}",
            source_urls=["https://example.com"],
            section="8",
        )
        assert chunk_id is not None


class TestCheckNegativeKnowledge:
    def test_check_negative_knowledge_finds_match(self):
        unique = uuid.uuid4().hex[:8]
        store_negative_knowledge(
            what_failed=f"journal editorial API integration workflow {unique}",
            reason="Too complex for MVP stage",
            source="kill_decision",
        )
        result = check_negative_knowledge(f"journal editorial API integration workflow {unique}")
        assert result is not None

    def test_check_negative_knowledge_no_false_positive(self):
        result = check_negative_knowledge(
            f"build a rocket to mars {uuid.uuid4().hex}"
        )
        assert result is None
