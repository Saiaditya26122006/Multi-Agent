#!/usr/bin/env python3
"""
BP.1.1 Pilot — Create atomic claims and link evidence.

This script demonstrates the end-to-end evidence chain for the BP.1.1
(Product Definition) node:

1. Create 5 atomic product claims
2. Query all BP.1.1 chunks from knowledge_base
3. For each chunk, determine which claims it supports
4. Create evidence_links with candidate_claim, sufficiency_status
5. Show the full evidence trail

Goal: Demonstrate that we can trace:
  Source Document → Extracted Fact → Evidence Tier → Link → Claim → Node
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from uuid import uuid4
from typing import Optional
from datetime import datetime, timezone
from services.rag_service import _get_supabase
from services.evidence_links import create_evidence_link, get_links_for_node

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# BP.1.1 Product Definition Node
BP11_NODE_ID = "BP.1.1"
BP11_CLAIMS = [
    {
        "claim_id": "BP11_C1",
        "text": "The system validates manuscript authenticity",
        "description": "Core function: the product must detect authentic vs inauthentic manuscripts",
        "evidence_tiers": ["E5", "E6", "E7"],  # requires data or test results
    },
    {
        "claim_id": "BP11_C2",
        "text": "Target market is research-active business schools",
        "description": "Primary buyer segment identified and validated",
        "evidence_tiers": ["E4", "E5", "E6"],  # third-party report or first-party data
    },
    {
        "claim_id": "BP11_C3",
        "text": "Pricing model is annual institutional subscription",
        "description": "Revenue model: per-institution annual fee",
        "evidence_tiers": ["E2", "E3", "E5"],  # CEO statement or contract
    },
    {
        "claim_id": "BP11_C4",
        "text": "Primary competitive advantage is detection speed",
        "description": "Differentiation: analysis turnaround faster than alternatives",
        "evidence_tiers": ["E5", "E6"],  # internal benchmark or third-party comparison
    },
    {
        "claim_id": "BP11_C5",
        "text": "Go-to-market includes direct sales and API licensing",
        "description": "Dual revenue stream: B2B direct + B2B2C via platform partners",
        "evidence_tiers": ["E3", "E4", "E5"],  # strategy doc or customer agreement
    },
]


def create_bp11_claims() -> dict:
    """Create atomic claims for BP.1.1 in a local structure (not persisted yet)."""
    logger.info("[BP.1.1 Pilot] Creating %d atomic claims", len(BP11_CLAIMS))
    return {claim["claim_id"]: claim for claim in BP11_CLAIMS}


def get_bp11_chunks() -> list[dict]:
    """Fetch all knowledge_base chunks related to BP.1.1."""
    try:
        sb = _get_supabase()
        result = sb.table('knowledge_base').select(
            'id', 'content', 'source_type', 'epistemic_status',
            'metadata'
        ).execute()

        # Chunks are tagged with metadata.node_id (there is no section_id key), and
        # the ids are hierarchical — BP.1.1's evidence lives on its descendants
        # (BP.1.1.1, BP.1.1.7.4, ...), not on a chunk labelled exactly "BP.1.1".
        bp11_chunks = []
        for chunk in result.data or []:
            metadata = chunk.get('metadata', {})
            if not isinstance(metadata, dict):
                continue
            node_id = str(metadata.get('node_id', ''))
            if node_id == 'BP.1.1' or node_id.startswith('BP.1.1.'):
                bp11_chunks.append(chunk)

        logger.info("[BP.1.1 Pilot] Found %d related chunks", len(bp11_chunks))
        return bp11_chunks
    except Exception as e:
        logger.error("[BP.1.1 Pilot] Failed to fetch chunks: %s", e)
        return []


def assess_chunk_claim_fit(chunk_content: str, claim: dict) -> Optional[str]:
    """
    Assess if a chunk supports a claim.
    Returns sufficiency_status: sufficient/partial/insufficient/blocked/untested
    """
    content_lower = chunk_content.lower()
    claim_keywords = claim["text"].lower().split()

    matched_keywords = sum(1 for kw in claim_keywords if kw in content_lower)
    match_ratio = matched_keywords / len(claim_keywords) if claim_keywords else 0

    if match_ratio >= 0.75:
        return "sufficient"
    elif match_ratio >= 0.5:
        return "partial"
    elif match_ratio > 0.25:
        return "insufficient"
    else:
        return "untested"


def link_chunk_to_claim(
    chunk_id: str,
    target_node_id: str,
    candidate_claim: str,
    sufficiency_status: str,
    chunk_content: str,
) -> Optional[str]:
    """Create an evidence_link from a chunk to a claim in a node."""
    try:
        link_id = create_evidence_link(
            chunk_id=chunk_id,
            target_node_id=target_node_id,
            candidate_claim=candidate_claim,
            claim_supported=chunk_content[:200] if chunk_content else None,
            sufficiency_status=sufficiency_status,
            boundary_reason=f"BP.1.1 pilot: {candidate_claim}",
            requires_corroboration=sufficiency_status in ["insufficient", "untested"],
        )
        return link_id
    except Exception as e:
        logger.error("[BP.1.1 Pilot] Failed to create link: %s", e)
        return None


def run_pilot():
    """Main pilot execution."""
    print("\n" + "="*70)
    print("BP.1.1 PRODUCT DEFINITION PILOT")
    print("="*70)

    # Step 1: Create claims
    claims = create_bp11_claims()
    print("\n✅ Created claims:")
    for claim_id, claim in claims.items():
        print(f"  {claim_id}: {claim['text']}")

    # Step 2: Get chunks
    chunks = get_bp11_chunks()
    if not chunks:
        print("\n⚠️  No BP.1.1 chunks found in knowledge_base")
        print("    Next step: Ingest CEO data using ingestion_pipeline")
        return

    print(f"\n✅ Found {len(chunks)} chunks")

    # Step 3: Link each chunk to claims
    links_created = 0
    coverage = {claim_id: [] for claim_id in claims.keys()}

    for chunk in chunks[:20]:  # Limit to first 20 for pilot
        chunk_id = chunk["id"]
        content = chunk.get("content", "")[:300]

        # Try to match with each claim
        for claim_id, claim in claims.items():
            sufficiency = assess_chunk_claim_fit(content, claim)
            if sufficiency != "untested":
                link_id = link_chunk_to_claim(
                    chunk_id=chunk_id,
                    target_node_id=BP11_NODE_ID,
                    candidate_claim=claim["text"],
                    sufficiency_status=sufficiency,
                    chunk_content=content,
                )
                if link_id:
                    coverage[claim_id].append((chunk_id[:8], sufficiency))
                    links_created += 1

    print(f"\n✅ Created {links_created} evidence_links")

    # Step 4: Show coverage
    print("\n📊 Claim Coverage Summary:")
    for claim_id, claim in claims.items():
        links = coverage[claim_id]
        status = "✅ Covered" if links else "❌ No evidence"
        print(f"\n  {claim_id}: {claim['text']}")
        print(f"     Status: {status}")
        if links:
            for chunk_id, sufficiency in links[:3]:
                print(f"       - {chunk_id}: {sufficiency}")
            if len(links) > 3:
                print(f"       ... +{len(links)-3} more")

    # Step 5: Show evidence tier distribution
    print("\n📈 Evidence Tier Distribution:")
    try:
        sb = _get_supabase()
        result = sb.table('evidence_links').select(
            'target_node_id', 'sufficiency_status'
        ).eq('target_node_id', BP11_NODE_ID).execute()

        if result.data:
            sufficiency_counts = {}
            for link in result.data:
                status = link['sufficiency_status']
                sufficiency_counts[status] = sufficiency_counts.get(status, 0) + 1

            for status, count in sorted(sufficiency_counts.items(), key=lambda x: -x[1]):
                print(f"  {status}: {count} links")
    except Exception as e:
        logger.warning("[BP.1.1 Pilot] Could not query evidence_links: %s", e)

    print("\n" + "="*70)
    print("NEXT STEPS:")
    print("  1. Fix RLS on evidence_links table (run fix_rls_system_tables.sql)")
    print("  2. Re-run this script to populate links")
    print("  3. View /api/evidence-links/BP.1.1 in web UI")
    print("="*70 + "\n")


if __name__ == "__main__":
    run_pilot()
