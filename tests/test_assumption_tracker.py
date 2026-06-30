"""
Tests for services/assumption_tracker.py

Requires live Supabase connection for integration tests.
"""

import uuid
import pytest

from services.assumption_tracker import (
    record_evidence,
    get_assumption_status,
)


class TestRecordEvidence:
    def test_record_evidence_stores_chunk(self):
        chunk_id = record_evidence(
            assumption_id=f"test_assumption_{uuid.uuid4().hex[:6]}",
            evidence="IESE dean confirmed interest in claim validation",
            effect="supports",
            source="interview",
        )
        assert chunk_id is not None
        assert len(chunk_id) == 36

    def test_invalid_effect_rejected(self):
        result = record_evidence(
            assumption_id="test_assumption",
            evidence="some evidence",
            effect="banana",
            source="test",
        )
        assert result is None


class TestGetAssumptionStatus:
    def test_get_status_no_evidence(self):
        status = get_assumption_status(f"nonexistent_{uuid.uuid4().hex}")
        assert status["current_status"] == "ASSUMPTION"
        assert status["confidence"] == 0.3
        assert status["evidence_count"] == 0

    def test_get_status_after_supports(self):
        aid = f"supported_assumption_{uuid.uuid4().hex[:6]}"
        record_evidence(aid, "First supporting evidence", "supports", "interview")
        record_evidence(aid, "Second supporting evidence", "supports", "pilot")

        status = get_assumption_status(aid)
        assert status["supports"] >= 1
        assert status["confidence"] > 0.3

    def test_get_status_after_invalidates(self):
        aid = f"invalid_assumption_{uuid.uuid4().hex[:6]}"
        record_evidence(aid, "Evidence that contradicts", "challenges", "interview")
        record_evidence(aid, "More contradicting evidence", "challenges", "advisor")

        status = get_assumption_status(aid)
        assert status["challenges"] >= 1
