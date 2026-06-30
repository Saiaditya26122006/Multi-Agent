"""
Integration tests — end-to-end RAG flows.

Tests the full lifecycle: store → retrieve, conversation round-trips,
kill → block, correction → supersede, and loader RAG integration.

Requires live Supabase connection.
"""

import uuid
import pytest

from agents.phase2.rag_mixin import rag_enrich, rag_check_killed
from services.conversation_store import (
    store_ceo_message,
    store_decision,
    store_correction,
    has_been_killed,
)
from services.rag_service import store, retrieve


class TestRagEnrich:
    def test_rag_enrich_returns_string(self):
        result = rag_enrich("pricing model monetization")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_rag_enrich_graceful_failure(self):
        from unittest.mock import patch
        with patch("agents.phase2.rag_mixin._rag_available", False):
            result = rag_enrich("anything")
            assert result == ""


class TestLoaderRAGIntegration:
    def test_loader_uses_rag_when_available(self):
        from ceo_data.loader import get_relevant_ceo_data
        result = get_relevant_ceo_data("8")
        assert isinstance(result, dict)
        if result.get("_rag"):
            assert "_chunk_count" in result

    def test_loader_falls_back_to_json(self):
        from ceo_data.loader import _get_via_json_fallback
        result = _get_via_json_fallback("3")
        assert isinstance(result, dict)
        assert len(result) > 0


class TestConversationRoundTrip:
    def test_conversation_round_trip(self):
        unique = uuid.uuid4().hex[:8]
        message = f"I want to focus on Spain and IESE business school first {unique}"
        store_ceo_message(message, session_id="test_roundtrip")

        results = retrieve(
            query=f"Spain IESE business school {unique}",
            source_types=["conversation"],
            top_k=5,
            threshold=0.3,
        )
        contents = " ".join(r.content for r in results)
        assert unique in contents


class TestKillBlocksFutureProposals:
    def test_kill_blocks_future_proposals(self):
        unique = uuid.uuid4().hex[:8]
        store_decision(
            proposal=f"B2C freemium researcher pricing model for individual academics {unique}",
            decision="kill",
            reasoning="Not viable, investors hate B2C edtech positioning",
        )
        result = has_been_killed(f"B2C freemium researcher pricing model for individual academics {unique}")
        assert result is True


class TestCorrectionSupersedesOld:
    def test_correction_supersedes_old(self):
        unique = uuid.uuid4().hex[:8]
        old_id = store(
            content=f"pricing is EUR 12000 per year {unique}",
            source_type="ceo_doc",
            section="10",
            deduplicate=False,
        )

        new_id = store_correction(
            original_fact=f"pricing is EUR 12000 per year {unique}",
            corrected_fact=f"pricing is EUR 8000 per year {unique}",
            original_chunk_id=old_id,
        )
        assert new_id is not None

        results = retrieve(
            query=f"pricing EUR year {unique}",
            top_k=10,
            threshold=0.3,
            exclude_superseded=True,
        )
        result_ids = [r.id for r in results]
        assert old_id not in result_ids
