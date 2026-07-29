"""
Tests for services/conversation_store.py

Requires live Supabase connection.
"""

import uuid
import pytest

from services.conversation_store import (
    store_ceo_message,
    store_system_question,
    store_ceo_answer,
    store_decision,
    store_correction,
    store_feedback,
    has_been_asked,
    has_been_killed,
)


class TestStoreCEOMessage:
    def test_store_ceo_message_basic(self):
        msg = f"I think we should focus on Spain first {uuid.uuid4().hex[:6]}"
        chunk_id = store_ceo_message(msg, session_id="test_session")
        assert chunk_id is not None
        assert len(chunk_id) == 36

    def test_store_ceo_message_too_short_skipped(self):
        result = store_ceo_message("hi")
        assert result is None

    def test_store_ceo_message_empty_skipped(self):
        result = store_ceo_message("")
        assert result is None


class TestStoreDecision:
    def test_store_decision_kill_is_negative_knowledge(self):
        proposal = f"per-claim pricing model {uuid.uuid4().hex[:6]}"
        chunk_id = store_decision(
            proposal=proposal,
            decision="kill",
            reasoning="Too granular for institutional buyers",
        )
        assert chunk_id is not None

    def test_store_decision_yes_is_decision_type(self):
        proposal = f"institutional subscription model {uuid.uuid4().hex[:6]}"
        chunk_id = store_decision(
            proposal=proposal,
            decision="yes",
            reasoning="Aligns with Turnitin analogy",
        )
        assert chunk_id is not None


class TestStoreCorrection:
    def test_store_correction_supersedes_old(self):
        from services.rag_service import store as rag_store

        unique = uuid.uuid4().hex[:6]
        old_id = rag_store(
            content=f"institutional pricing is twelve thousand euros per year for business schools {unique}",
            source_type="ceo_doc",
            deduplicate=False,
        ).id
        assert old_id is not None
        new_id = store_correction(
            original_fact=f"institutional pricing is twelve thousand euros {unique}",
            corrected_fact=f"institutional pricing is eight thousand euros {unique}",
            original_chunk_id=old_id,
        )
        assert new_id is not None


class TestStoreCEOAnswer:
    def test_store_ceo_answer_pairs_qa(self):
        chunk_id = store_ceo_answer(
            question="What is your target geography?",
            answer=f"Spain and EU first {uuid.uuid4().hex[:6]}",
        )
        assert chunk_id is not None


class TestHasBeenAsked:
    def test_has_been_asked_true(self):
        unique = uuid.uuid4().hex[:8]
        store_ceo_answer(
            question=f"What is the institutional pricing model for EpistemicOS {unique}?",
            answer=f"CEO answered: Institutional annual subscription model for universities {unique}",
        )
        result = has_been_asked(f"institutional pricing model EpistemicOS {unique}")
        assert result is True

    def test_has_been_asked_false_when_no_match(self):
        result = has_been_asked(f"completely_random_topic_{uuid.uuid4().hex}")
        assert result is False


class TestHasBeenKilled:
    def test_has_been_killed_true(self):
        unique = uuid.uuid4().hex[:8]
        store_decision(
            proposal=f"freemium pricing model for individual academic researchers {unique}",
            decision="kill",
            reasoning="Not viable for this market, investors dislike B2C edtech",
        )
        result = has_been_killed(f"freemium pricing model for individual academic researchers {unique}")
        assert result is True

    def test_has_been_killed_catches_rephrasing(self):
        """The guard exists so killed ideas are not re-suggested, and an agent will
        re-propose in its own words rather than Alex's. The old 0.65 threshold sat
        above every rephrasing — and above the exact wording (0.649) — so it never
        fired and killed ideas came back.
        """
        store_decision(
            proposal="freemium pricing model for individual academic researchers",
            decision="kill",
            reasoning="Not viable for this market, investors dislike B2C edtech",
        )
        assert has_been_killed("offer a freemium plan to individual researchers") is True
        assert has_been_killed("B2C pricing aimed at solo researchers") is True

    def test_has_been_killed_ignores_unrelated_proposals(self):
        """Guarding too broadly is its own failure — it would block ideas Alex
        never killed."""
        assert has_been_killed("hire a senior backend engineer in month 3") is False
        assert has_been_killed("office space lease in central London") is False

    def test_has_been_killed_false(self):
        result = has_been_killed(f"totally_unrelated_{uuid.uuid4().hex}")
        assert result is False
