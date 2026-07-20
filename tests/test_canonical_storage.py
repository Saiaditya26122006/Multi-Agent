"""
Tests for Canonical Storage Service

Tests the bidirectional linking between canonical tables and knowledge_base.
"""

import sys
import os
import pytest
import uuid
from datetime import datetime

# Add parent directory to path to import memory module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.canonical_storage import CanonicalStorage, CanonicalStorageError
from memory.supabase_client import supabase as db


@pytest.fixture
def canonical_storage():
    """Create a CanonicalStorage instance"""
    return CanonicalStorage()


@pytest.fixture
def test_session_id():
    """Create a test session"""
    # Get first CEO from database
    ceo_context = db.table("ceo_context").select("id").limit(1).execute()
    if not ceo_context.data:
        pytest.skip("No CEO context found in database")

    ceo_id = ceo_context.data[0]["id"]

    session = db.table("sessions").insert({
        "ceo_id": ceo_id,
        "state": "AWAITING_RESEARCH",
        "chat_id": "999999999",
    }).execute()
    return session.data[0]["id"]


def test_store_assumption_creates_canonical_and_kb(canonical_storage, test_session_id):
    """Verify assumption stored in both canonical + KB with bidirectional link"""

    assumption_id, kb_id = canonical_storage.store_assumption(
        session_id=test_session_id,
        content="Market size is €50M",
        status="validated",
        source="ceo_input",
        confidence=0.9,
        created_by="Alex",
    )

    assert assumption_id is not None, "assumption_id should not be None"
    assert kb_id is not None, "kb_id should not be None"

    # Check: assumption table has KB link
    assumption = db.table("assumptions").select("*").eq("id", assumption_id).single().execute()
    assert assumption.data is not None, "Assumption not found in canonical table"
    assert assumption.data["knowledge_base_id"] == kb_id, "Assumption should link to KB"
    assert assumption.data["content"] == "Market size is €50M", "Content mismatch"
    assert assumption.data["confidence"] == 0.9, "Confidence mismatch"

    # Check: KB entry has assumption link
    kb_entry = db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()
    assert kb_entry.data is not None, "KB entry not found"
    assert kb_entry.data["assumption_id"] == assumption_id, "KB should link back to assumption"
    assert kb_entry.data["epistemic_status"] == "ASSUMPTION", "Status should be ASSUMPTION"
    assert kb_entry.data["source_type"] == "assumption_lifecycle", "Source type mismatch"


def test_store_decision_creates_canonical_and_kb(canonical_storage, test_session_id):
    """Verify decision stored correctly with bidirectional link"""

    decision_id, kb_id = canonical_storage.store_decision(
        session_id=test_session_id,
        section_id="BP.1",
        title="Go with SaaS pricing",
        reasoning="Market expects SaaS, not perpetual",
        decision_type="yes",
        created_by="Alex",
    )

    assert decision_id is not None, "decision_id should not be None"
    assert kb_id is not None, "kb_id should not be None"

    # Check: canonical table linked to KB
    decision = db.table("decisions").select("*").eq("id", decision_id).single().execute()
    assert decision.data is not None, "Decision not found in canonical table"
    assert decision.data["knowledge_base_id"] == kb_id, "Decision should link to KB"
    assert decision.data["status"] == "yes", "Decision status should be 'yes'"

    # Check: KB entry marked as CONFIRMED (CEO decision)
    kb_entry = db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()
    assert kb_entry.data is not None, "KB entry not found"
    assert kb_entry.data["decision_id"] == decision_id, "KB should link back to decision"
    assert kb_entry.data["epistemic_status"] == "CONFIRMED", "Status should be CONFIRMED"
    assert kb_entry.data["confidence"] == 0.95, "Confidence should be 0.95 for CEO decisions"


def test_store_agent_output_creates_canonical_and_kb(canonical_storage, test_session_id):
    """Verify agent output stored correctly with bidirectional link"""

    output_data = {
        "market_size": "€50M",
        "growth_rate": "23% CAGR",
        "confidence_score": 0.75,
    }

    output_id, kb_id = canonical_storage.store_agent_output(
        session_id=test_session_id,
        run_id="run-123",
        section_id="BP.1",
        agent_name="opportunity_analyst",
        output_json=output_data,
        confidence=0.75,
    )

    assert output_id is not None, "output_id should not be None"
    assert kb_id is not None, "kb_id should not be None"

    # Check: canonical table
    output = db.table("agent_outputs").select("*").eq("id", output_id).single().execute()
    assert output.data is not None, "Output not found in canonical table"
    assert output.data["knowledge_base_id"] == kb_id, "Output should link to KB"
    assert output.data["agent_name"] == "opportunity_analyst", "Agent name mismatch"

    # Check: KB entry marked as INFERRED (agent-produced)
    kb_entry = db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()
    assert kb_entry.data is not None, "KB entry not found"
    assert kb_entry.data["output_id"] == output_id, "KB should link back to output"
    assert kb_entry.data["epistemic_status"] == "INFERRED", "Status should be INFERRED"
    assert kb_entry.data["source_type"] == "agent_insight", "Source type mismatch"


def test_task_separate_from_kb(canonical_storage, test_session_id):
    """Verify tasks stored ONLY in tasks table, NOT in KB"""

    task_id = canonical_storage.create_task(
        session_id=test_session_id,
        title="Schedule customer interviews",
        task_type="interview",
        relates_to_node="BP.5",
        priority=1,
        created_by="mother_agent",
    )

    assert task_id is not None, "task_id should not be None"

    # Task exists in tasks table
    task = db.table("tasks").select("*").eq("id", task_id).single().execute()
    assert task.data is not None, "Task not found in tasks table"
    assert task.data["title"] == "Schedule customer interviews", "Title mismatch"
    assert task.data["relates_to_node"] == "BP.5", "Related node mismatch"

    # Task NOT in KB (actions are not evidence)
    kb_query = db.table("knowledge_base").select("*").eq("task_id", task_id).execute()
    assert len(kb_query.data) == 0, "Task should NOT be stored in KB"


def test_contradiction_filed_to_bp12(canonical_storage, test_session_id):
    """Verify contradictions filed to BP.12 register correctly"""

    # Create two KB chunks (simulating contradictory facts)
    chunk1_data = {
        "content": "Price is €100",
        "source_type": "ceo_doc",
        "session_id": test_session_id,
        "epistemic_status": "CONFIRMED",
        "confidence": 0.9,
    }
    chunk1 = db.table("knowledge_base").insert(chunk1_data).execute()
    chunk1_id = chunk1.data[0]["id"]

    chunk2_data = {
        "content": "Price is €150",
        "source_type": "ceo_doc",
        "session_id": test_session_id,
        "epistemic_status": "CONFIRMED",
        "confidence": 0.9,
    }
    chunk2 = db.table("knowledge_base").insert(chunk2_data).execute()
    chunk2_id = chunk2.data[0]["id"]

    # File contradiction
    bp12_id = canonical_storage.file_contradiction(
        session_id=test_session_id,
        bp_node="BP.12",  # Auto-assign
        issue_type="contradiction",
        title="Price conflict",
        chunk1_id=chunk1_id,
        chunk2_id=chunk2_id,
        description="Two different prices mentioned",
    )

    assert bp12_id is not None, "bp12_id should not be None"

    # Verify filed
    record = db.table("bp12_register").select("*").eq("id", bp12_id).single().execute()
    assert record.data is not None, "BP.12 record not found"
    assert record.data["bp_node"] == "BP.12.1", "BP node should be auto-assigned to BP.12.1"
    assert record.data["issue_type"] == "contradiction", "Issue type should be contradiction"
    assert record.data["status"] == "open", "Status should be open"
    assert record.data["primary_chunk_id"] == chunk1_id, "Primary chunk mismatch"
    assert record.data["conflicting_chunk_id"] == chunk2_id, "Conflicting chunk mismatch"


def test_file_gap_to_bp12(canonical_storage, test_session_id):
    """Verify gaps filed to BP.12 register correctly"""

    gap_id = canonical_storage.file_contradiction(
        session_id=test_session_id,
        bp_node="BP.12",
        issue_type="gap",
        title="Missing customer interview data",
        chunk1_id=None,
        description="Need to conduct interviews with 5+ customers",
    )

    assert gap_id is not None, "gap_id should not be None"

    # Verify filed
    record = db.table("bp12_register").select("*").eq("id", gap_id).single().execute()
    assert record.data is not None, "BP.12 gap record not found"
    assert record.data["issue_type"] == "gap", "Issue type should be gap"
    assert record.data["bp_node"] == "BP.12.2", "Should auto-assign next BP.12 node"


def test_verify_canonical_structure(canonical_storage):
    """Verify health check returns structure status"""

    status = canonical_storage.verify_canonical_structure()

    assert status is not None, "Status should not be None"
    assert "structure_valid" in status, "Status should include structure_valid"
    assert status["structure_valid"] is True, "Structure should be valid"
    assert "linked_count" in status, "Status should include linked_count"
    assert isinstance(status["linked_count"], int), "linked_count should be int"


def test_multiple_assumptions_bidirectional(canonical_storage, test_session_id):
    """Verify multiple assumptions each have proper bidirectional links"""

    # Store 3 assumptions
    ids = []
    for i in range(3):
        assumption_id, kb_id = canonical_storage.store_assumption(
            session_id=test_session_id,
            content=f"Assumption {i+1}",
            status="validated",
            source="test",
            confidence=0.7,
        )
        ids.append((assumption_id, kb_id))

    # Verify each pair is linked
    for assumption_id, kb_id in ids:
        assumption = db.table("assumptions").select("*").eq("id", assumption_id).single().execute()
        kb_entry = db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()

        assert assumption.data["knowledge_base_id"] == kb_id, f"Assumption {assumption_id} not linked to KB"
        assert kb_entry.data["assumption_id"] == assumption_id, f"KB {kb_id} not linked back to assumption"


def test_error_handling_invalid_session(canonical_storage):
    """Verify error handling for invalid session"""

    with pytest.raises(CanonicalStorageError):
        canonical_storage.store_assumption(
            session_id="00000000-0000-0000-0000-000000000000",
            content="Test",
            status="validated",
            source="test",
        )
