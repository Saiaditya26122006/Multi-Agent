"""
Tests for Contradiction Detector Service (Hybrid LLM Judge)

Tests the complete pipeline:
1. Semantic pre-filter (find candidates)
2. LLM judge (validate contradictions)
3. Filing to BP.12
4. Resolution workflow
"""

import sys
import os
import pytest
import uuid

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.contradiction_detector import ContradictionDetector, ContradictionDetectorError
from memory.supabase_client import supabase as db


@pytest.fixture
def detector():
    """Create a ContradictionDetector instance"""
    return ContradictionDetector()


@pytest.fixture
def test_session_id(admin_db):
    """Create a test session"""
    ceo_context = db.table("ceo_context").select("id").limit(1).execute()
    if not ceo_context.data:
        pytest.skip("No CEO context found")

    ceo_id = ceo_context.data[0]["id"]

    session = admin_db.table("sessions").insert({
        "ceo_id": ceo_id,
        "state": "AWAITING_RESEARCH",
        "telegram_chat_id": "999999999",
    }).execute()
    return session.data[0]["id"] if session.data else None


@pytest.fixture
def test_kb_entries(test_session_id, admin_db):
    """Create test KB entries with contradictions"""
    entries = []

    contradictory_pairs = [
        ("Market size is €50M", "Market size is €100M", "BP.1.1"),
        ("Price point is €99", "Price point is €149", "BP.5.2"),
        ("Launch in Q1 2026", "Launch in Q2 2026", "BP.7.1"),
    ]

    for content1, content2, section in contradictory_pairs:
        # Entry 1
        data1 = {
            "content": content1,
            "source_type": "agent_insight",
            "session_id": test_session_id,
            "section": section,
            "epistemic_status": "CONFIRMED",
            "confidence": 0.9,
        }
        result1 = admin_db.table("knowledge_base").insert(data1).execute()
        if result1.data:
            entries.append(result1.data[0])

        # Entry 2
        data2 = {
            "content": content2,
            "source_type": "agent_insight",
            "session_id": test_session_id,
            "section": section,
            "epistemic_status": "CONFIRMED",
            "confidence": 0.85,
        }
        result2 = admin_db.table("knowledge_base").insert(data2).execute()
        if result2.data:
            entries.append(result2.data[0])

    return entries


def test_cosine_similarity(detector):
    """Verify cosine similarity calculation"""
    embed1 = [1.0, 0.0, 0.0]
    embed2 = [1.0, 0.0, 0.0]

    similarity = detector._cosine_similarity(embed1, embed2)
    assert similarity == 1.0, "Identical embeddings should have similarity 1.0"

    embed3 = [0.0, 1.0, 0.0]
    similarity = detector._cosine_similarity(embed1, embed3)
    assert similarity == 0.0, "Orthogonal embeddings should have similarity 0.0"


def test_detect_and_file_all(detector, test_session_id, test_kb_entries):
    """Verify full pipeline: detect → judge → file"""

    results = detector.detect_and_file_all(
        session_id=test_session_id,
        pre_filter_threshold=0.70,
        llm_confidence_threshold=0.75,
    )

    assert isinstance(results, dict), "Results should be a dictionary"
    assert "detected_count" in results, "Should have detected_count"
    assert "filed_count" in results, "Should have filed_count"
    assert "by_impact" in results, "Should have by_impact breakdown"
    assert "by_type" in results, "Should have by_type breakdown"
    assert isinstance(results["by_impact"], dict), "Impact counts should be dict"


def test_get_bp_contradictions(detector, test_session_id):
    """Verify retrieval of filed contradictions"""

    contradictions = detector.get_bp_contradictions(
        bp_node="BP.12",
        status="open",
    )

    assert isinstance(contradictions, list), "Should return list"
    for contra in contradictions:
        assert "issue_type" in contra, "Should have issue_type"
        assert contra.get("issue_type") == "contradiction", "Should be contradiction type"


def test_resolve_contradiction(detector):
    """Verify contradiction resolution workflow"""

    # Get an open contradiction to resolve
    contradictions = detector.get_bp_contradictions(
        bp_node="BP.12",
        status="open",
    )

    if not contradictions:
        pytest.skip("No open contradictions to resolve")

    contra = contradictions[0]

    # Resolve it
    resolution = detector.resolve_contradiction(
        bp12_record_id=contra["id"],
        resolution_type="accepted",
        ceo_decision="The €100M figure is correct.",
        canonical_value="Market size is €100M",
    )

    assert resolution["status"] == "resolved", "Should be marked resolved"
    assert resolution["resolution_type"] == "accepted", "Should be accepted type"
    assert resolution["bp12_record_id"] == contra["id"], "Should match record ID"


def test_judge_contradiction(detector):
    """Verify LLM judge for contradiction validation"""

    chunk1 = {
        "id": str(uuid.uuid4()),
        "content": "Market size is €50M according to latest research.",
        "source_type": "agent_insight",
        "section": "BP.1.1",
        "confidence": 0.9,
    }

    chunk2 = {
        "id": str(uuid.uuid4()),
        "content": "Market size is estimated at €100M by competitor analysis.",
        "source_type": "agent_insight",
        "section": "BP.1.1",
        "confidence": 0.85,
    }

    judgment = detector._judge_contradiction(chunk1, chunk2)

    assert isinstance(judgment, dict), "Judgment should be dict"
    assert "is_contradiction" in judgment, "Should have is_contradiction"
    assert "confidence" in judgment, "Should have confidence score"
    assert "reasoning" in judgment, "Should have reasoning"


def test_error_handling_invalid_record(detector):
    """Verify error handling for invalid record ID"""

    with pytest.raises(ContradictionDetectorError):
        detector.resolve_contradiction(
            bp12_record_id="invalid-id-xyz",
            resolution_type="accepted",
            ceo_decision="Test",
        )


def test_similarity_with_none_embeddings(detector):
    """Verify similarity handles missing embeddings gracefully"""

    similarity = detector._cosine_similarity(None, [1.0, 0.0])
    assert similarity == 0.0, "None embedding should return 0.0"

    similarity = detector._cosine_similarity([1.0, 0.0], None)
    assert similarity == 0.0, "None embedding should return 0.0"
