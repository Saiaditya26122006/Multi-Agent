"""
Tests for BP Node Registry Service

Tests linking KB entries to business plan nodes and registry operations.
"""

import sys
import os
import pytest
import uuid

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.bp_node_registry import BPNodeRegistry, BPNodeRegistryError
from memory.supabase_client import supabase as db


@pytest.fixture
def bp_registry():
    """Create a BPNodeRegistry instance"""
    return BPNodeRegistry()


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


@pytest.fixture
def test_kb_entry(test_session_id, admin_db):
    """Create a test KB entry"""
    kb_data = {
        "content": "Test KB entry for BP.1.1.1",
        "source_type": "agent_insight",
        "session_id": test_session_id,
        "section": "BP.1.1.1",
        "epistemic_status": "CONFIRMED",
        "confidence": 0.9,
    }
    result = admin_db.table("knowledge_base").insert(kb_data).execute()
    return result.data[0]["id"] if result.data else None


def test_link_kb_to_bp_node(bp_registry, test_kb_entry):
    """Verify KB entry links to correct BP node"""

    kb_id, bp_id = bp_registry.link_kb_to_bp_node(
        kb_id=test_kb_entry,
        section_id="BP.1.1.1",
    )

    assert kb_id == test_kb_entry, "KB ID should match input"
    assert bp_id is not None, "BP node should be found"

    # Verify link in database
    kb_entry = db.table("knowledge_base").select("bp_section_id").eq("id", kb_id).single().execute()
    assert kb_entry.data["bp_section_id"] == bp_id, "KB should be linked to BP node"


def test_link_kb_missing_bp_node(bp_registry, test_kb_entry):
    """Verify graceful handling when BP node doesn't exist"""

    kb_id, bp_id = bp_registry.link_kb_to_bp_node(
        kb_id=test_kb_entry,
        section_id="BP.99.99.99",  # Non-existent node
    )

    assert kb_id == test_kb_entry, "KB ID should match"
    assert bp_id is None, "Should return None for missing BP node"


def test_link_kb_batch_by_section(bp_registry, test_session_id, admin_db):
    """Verify batch linking of KB entries to BP nodes"""

    # Create a few test KB entries with section IDs
    kb_ids = []
    for i, section in enumerate(["BP.1.1.1", "BP.1.1.2", "BP.1.1.3"]):
        kb_data = {
            "content": f"Test KB entry {i} for {section}",
            "source_type": "agent_insight",
            "session_id": test_session_id,
            "section": section,
            "epistemic_status": "CONFIRMED",
            "confidence": 0.9,
        }
        result = admin_db.table("knowledge_base").insert(kb_data).execute()
        if result.data:
            kb_ids.append(result.data[0]["id"])

    # Run batch linking
    results = bp_registry.link_kb_batch_by_section(session_id=test_session_id)

    assert results["linked_count"] >= 0, "Should report linked count"
    assert results["error_count"] == 0, "Should have no errors"
    assert isinstance(results["missing_count"], int), "Should report missing count"


def test_get_bp_node_hierarchy(bp_registry):
    """Verify BP node hierarchy retrieval"""

    hierarchy = bp_registry.get_bp_node_hierarchy("BP.1")

    assert hierarchy["node"] is not None, "Node should exist"
    assert hierarchy["node"]["section_id"] == "BP.1", "Section ID should match"
    assert isinstance(hierarchy["children"], list), "Children should be a list"
    assert isinstance(hierarchy["evidence_count"], int), "Evidence count should be int"


def test_get_bp_node_hierarchy_with_parent(bp_registry):
    """Verify parent node is included when exists"""

    hierarchy = bp_registry.get_bp_node_hierarchy("BP.1.1.1")

    assert hierarchy["node"] is not None, "Node should exist"
    assert hierarchy["parent"] is not None, "Parent should exist for leaf node"
    assert hierarchy["parent"]["section_id"] == "BP.1.1", "Parent section should be BP.1.1"


def test_get_bp_domain_coverage(bp_registry):
    """Verify domain coverage reporting"""

    coverage = bp_registry.get_bp_domain_coverage()

    assert isinstance(coverage, dict), "Coverage should be a dictionary"
    assert len(coverage) > 0, "Should have at least one domain"
    # Check for BP domains (BP.1, BP.2, ... BP.12)
    assert any(d.startswith("BP.") for d in coverage.keys()), "Should include BP domains"

    for domain, stats in coverage.items():
        assert "total_nodes" in stats, f"Domain {domain} should have total_nodes"
        assert "linked_count" in stats, f"Domain {domain} should have linked_count"
        assert "coverage" in stats, f"Domain {domain} should have coverage %"
        assert isinstance(stats["coverage"], (int, float)), "Coverage should be numeric"
        assert 0 <= stats["coverage"] <= 1, "Coverage should be between 0-1"


def test_verify_registry_integrity(bp_registry):
    """Verify registry integrity check"""

    integrity = bp_registry.verify_registry_integrity()

    assert integrity["total_bp_nodes"] == 840, "Should have 840 BP nodes"
    assert isinstance(integrity["total_kb_linked"], int), "Linked count should be int"
    assert isinstance(integrity["orphaned_kb_count"], int), "Orphaned count should be int"
    assert isinstance(integrity["unlinked_kb_count"], int), "Unlinked count should be int"
    assert isinstance(integrity["integrity_valid"], bool), "Integrity flag should be bool"


def test_error_handling_invalid_node(bp_registry):
    """Verify error handling for invalid node IDs"""

    with pytest.raises(BPNodeRegistryError):
        bp_registry.get_bp_node_hierarchy("INVALID.NODE.ID")


def test_kb_batch_without_section(bp_registry, test_session_id, admin_db):
    """Verify batch linking handles KB entries without section"""

    # Create KB entry without section
    kb_data = {
        "content": "KB entry without section",
        "source_type": "agent_insight",
        "session_id": test_session_id,
        "epistemic_status": "CONFIRMED",
        "confidence": 0.9,
    }
    result = admin_db.table("knowledge_base").insert(kb_data).execute()

    # Run batch linking
    results = bp_registry.link_kb_batch_by_section(session_id=test_session_id)

    assert results["error_count"] == 0, "Should handle missing section gracefully"
    assert results["missing_count"] >= 1, "Should count entries without section"


def test_bp_node_full_hierarchy(bp_registry):
    """Verify full hierarchy chain BP.1 -> BP.1.1 -> BP.1.1.1"""

    # Get BP.1.1.1
    h1 = bp_registry.get_bp_node_hierarchy("BP.1.1.1")
    assert h1["parent"]["section_id"] == "BP.1.1", "Parent should be BP.1.1"

    # Get BP.1.1
    h2 = bp_registry.get_bp_node_hierarchy("BP.1.1")
    assert h2["parent"]["section_id"] == "BP.1", "Parent should be BP.1"

    # Get BP.1
    h3 = bp_registry.get_bp_node_hierarchy("BP.1")
    assert h3["parent"] is None, "BP.1 should have no parent"
    assert len(h3["children"]) > 0, "BP.1 should have children"
