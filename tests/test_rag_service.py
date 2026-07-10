"""
Unit + integration tests for services/rag_service.py

Tests 1-4 and 15-16 are pure unit tests (no Supabase needed).
Tests 5-14 require a live Supabase connection with knowledge_base table.
"""

import math
import uuid
import pytest

from services.rag_service import (
    embed,
    content_hash,
    format_chunks_for_injection,
    store,
    batch_store,
    retrieve,
    supersede,
    Chunk,
    VALID_SOURCE_TYPES,
)


# ── Pure unit tests (no DB) ──────────────────────────────────────────────────


class TestEmbed:
    def test_embed_returns_1024_dims(self):
        result = embed("hello world")
        assert len(result) == 1024

    def test_embed_returns_valid_floats(self):
        result = embed("test embedding values")
        assert all(isinstance(x, float) for x in result)

    def test_embed_different_texts_differ(self):
        v1 = embed("The cat sat on the mat")
        v2 = embed("Quantum mechanics and string theory")
        assert v1 != v2

    def test_embed_similar_texts_close(self):
        v1 = embed("pricing model for SaaS subscription")
        v2 = embed("monetization strategy for software as a service")
        cosine = sum(a * b for a, b in zip(v1, v2))
        assert cosine > 0.3


class TestContentHash:
    def test_content_hash_deterministic(self):
        h1 = content_hash("same content")
        h2 = content_hash("same content")
        assert h1 == h2

    def test_content_hash_differs_for_different_content(self):
        h1 = content_hash("content A")
        h2 = content_hash("content B")
        assert h1 != h2


class TestFormatChunks:
    def test_format_chunks_for_injection(self):
        chunks = [
            Chunk(
                id="1", content="First fact about pricing",
                source_type="ceo_doc", similarity=0.9,
                epistemic_status="CONFIRMED",
            ),
            Chunk(
                id="2", content="Second fact about market" * 50,
                source_type="ceo_doc", similarity=0.8,
                epistemic_status="ASSUMPTION",
            ),
        ]
        result = format_chunks_for_injection(chunks, max_chars=200)
        assert len(result) <= 200
        assert "First fact" in result


# ── Integration tests (require Supabase) ──────────────────────────────────────


class TestStore:
    def test_store_returns_uuid(self):
        chunk_id = store(
            content=f"test_store_{uuid.uuid4().hex[:8]}",
            source_type="ceo_doc",
            section="test",
            epistemic_status="CONFIRMED",
            deduplicate=False,
        )
        assert chunk_id is not None
        assert len(chunk_id) == 36  # UUID format

    def test_store_deduplication(self):
        unique_content = f"dedup_test_{uuid.uuid4().hex[:8]}"
        id1 = store(content=unique_content, source_type="ceo_doc", deduplicate=True)
        id2 = store(content=unique_content, source_type="ceo_doc", deduplicate=True)
        assert id1 is not None
        assert id2 is None

    def test_store_invalid_source_type_raises(self):
        with pytest.raises(ValueError):
            store(content="test", source_type="invalid_type_xyz")

    def test_store_empty_content_skipped(self):
        result = store(content="", source_type="ceo_doc")
        assert result is None

        result2 = store(content="   ", source_type="ceo_doc")
        assert result2 is None


class TestBatchStore:
    def test_batch_store_multiple(self):
        chunks = [
            {"content": f"batch_test_item_{i}_{uuid.uuid4().hex[:6]}", "source_type": "ceo_doc"}
            for i in range(5)
        ]
        ids = batch_store(chunks)
        assert len(ids) == 5
        assert all(len(i) == 36 for i in ids)


class TestRetrieve:
    def test_retrieve_finds_stored_content(self):
        unique = f"unique_retrieval_test_{uuid.uuid4().hex[:8]}"
        store(
            content=f"EpistemicOS pricing is institutional subscription {unique}",
            source_type="ceo_doc",
            section="10",
            deduplicate=False,
        )
        results = retrieve(
            query=f"institutional subscription pricing {unique}",
            top_k=5,
            threshold=0.3,
        )
        contents = [r.content for r in results]
        assert any(unique in c for c in contents)

    def test_retrieve_respects_threshold(self):
        results = retrieve(
            query="quantum physics dark matter string theory",
            source_types=["ceo_doc"],
            top_k=5,
            threshold=0.8,
        )
        assert len(results) == 0

    def test_retrieve_filters_source_type(self):
        unique = f"filter_test_{uuid.uuid4().hex[:8]}"
        store(content=f"conversation filter {unique}", source_type="conversation", deduplicate=False)
        store(content=f"ceo_doc filter {unique}", source_type="ceo_doc", deduplicate=False)

        results = retrieve(
            query=f"filter {unique}",
            source_types=["conversation"],
            top_k=10,
            threshold=0.3,
        )
        for r in results:
            assert r.source_type == "conversation"

    def test_retrieve_excludes_superseded(self):
        unique = f"supersede_test_{uuid.uuid4().hex[:8]}"
        old_id = store(content=f"old fact {unique}", source_type="ceo_doc", deduplicate=False)
        new_id = store(content=f"new fact {unique}", source_type="ceo_doc", deduplicate=False)
        supersede(old_id, new_id)

        results = retrieve(query=f"fact {unique}", top_k=10, threshold=0.3)
        result_ids = [r.id for r in results]
        assert old_id not in result_ids


class TestSupersede:
    def test_supersede_marks_old_chunk(self):
        old_id = store(
            content=f"to_supersede_{uuid.uuid4().hex[:8]}",
            source_type="ceo_doc",
            deduplicate=False,
        )
        new_id = store(
            content=f"replacement_{uuid.uuid4().hex[:8]}",
            source_type="ceo_doc",
            deduplicate=False,
        )
        result = supersede(old_id, new_id)
        assert result is True
