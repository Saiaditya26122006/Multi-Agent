"""
Performance and accuracy tests for the RAG system.

Tests retrieval latency, embedding speed, and result quality.
Requires live Supabase connection with ingested data.

On latency budgets: embedding is a Bedrock (Titan v2) round trip and retrieval is
that plus a Supabase RPC, so both are dominated by network distance to us-east-1
rather than by our code — a warm embed measures ~380ms from Europe/Asia and would
measure far less from inside us-east-1. The old 50ms budget dates from when
embedding ran locally on all-MiniLM-L6-v2; it has been unreachable since the move
to Titan and simply failed everywhere. These budgets are set to catch a real
regression (an order of magnitude, a lost singleton, a retry storm) without
failing on geography. Both tests warm up first, because the first call pays client
construction and the TLS handshake (~1.7s) and that is not what they measure.
"""

import time
import pytest

from services.rag_service import embed, retrieve

# Generous on purpose — see the module docstring.
EMBED_BUDGET_MS = 2000
RETRIEVAL_BUDGET_MS = 3000


class TestLatency:
    def test_retrieval_latency(self):
        queries = [
            "pricing model institutional subscription",
            "GDPR compliance data processing",
            "business school pilot validation",
            "competitive landscape manuscript diagnostics",
            "team structure hiring plan",
            "financial projections revenue costs",
            "journal editorial workflow integration",
            "claim extraction accuracy validation",
            "Spain EU geography market",
            "researcher doctoral student user",
        ]

        retrieve("warmup", top_k=1, threshold=0.3)  # pay client init + TLS once

        times = []
        for q in queries:
            start = time.time()
            retrieve(q, top_k=5, threshold=0.3)
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)

        avg_ms = sum(times) / len(times)
        assert avg_ms < RETRIEVAL_BUDGET_MS, (
            f"Average retrieval latency {avg_ms:.0f}ms exceeds "
            f"{RETRIEVAL_BUDGET_MS}ms — retrieval is one embed plus one RPC, so "
            f"this suggests a real regression rather than network distance"
        )

    def test_embed_latency(self):
        texts = [
            "pricing model for academic institutions",
            "GDPR compliance requirements",
            "manuscript diagnostics platform",
            "claim-level epistemic validation",
            "pre-submission reviewer readiness",
            "institutional SaaS subscription",
            "research integrity infrastructure",
            "editorial triage support",
            "business school pilot program",
            "competitive differentiation strategy",
        ]

        embed("warmup")  # pay client init + TLS once

        times = []
        for t in texts:
            start = time.time()
            embed(t)
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)

        avg_ms = sum(times) / len(times)
        assert avg_ms < EMBED_BUDGET_MS, (
            f"Average embed latency {avg_ms:.0f}ms exceeds {EMBED_BUDGET_MS}ms — "
            f"a warm Titan call is a single round trip, so this suggests the client "
            f"singleton is being rebuilt or calls are being retried"
        )


class TestAccuracy:
    def test_relevant_chunks_in_top_5(self):
        results = retrieve(
            query="pricing model monetization subscription",
            source_types=["ceo_doc"],
            top_k=5,
            threshold=0.3,
        )
        assert len(results) > 0
        contents = " ".join(r.content.lower() for r in results)
        pricing_terms = ["pricing", "monetization", "subscription", "institution", "unit"]
        matches = sum(1 for term in pricing_terms if term in contents)
        assert matches >= 2, f"Expected >=2 pricing terms in results, got {matches}"

    def test_irrelevant_query_returns_few(self):
        results = retrieve(
            query="quantum physics dark matter string theory Mars colonization",
            source_types=["ceo_doc"],
            top_k=5,
            threshold=0.6,
        )
        assert len(results) <= 1
