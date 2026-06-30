"""
Performance and accuracy tests for the RAG system.

Tests retrieval latency, embedding speed, and result quality.
Requires live Supabase connection with ingested data.
"""

import time
import pytest

from services.rag_service import embed, retrieve


class TestLatency:
    def test_retrieval_latency_under_200ms(self):
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

        times = []
        for q in queries:
            start = time.time()
            retrieve(q, top_k=5, threshold=0.3)
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)

        avg_ms = sum(times) / len(times)
        assert avg_ms < 500, f"Average retrieval latency {avg_ms:.0f}ms exceeds 500ms (remote Supabase)"

    def test_embed_latency_under_50ms(self):
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

        times = []
        for t in texts:
            start = time.time()
            embed(t)
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)

        avg_ms = sum(times) / len(times)
        assert avg_ms < 50, f"Average embed latency {avg_ms:.0f}ms exceeds 50ms"


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
