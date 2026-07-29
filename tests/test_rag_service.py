"""
Unit + integration tests for services/rag_service.py

Tests 1-4 and 15-16 are pure unit tests (no Supabase needed).
Tests 5-14 require a live Supabase connection with knowledge_base table.
"""

import math
import uuid
from unittest.mock import MagicMock, patch

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
    RagStoreError,
    StoreOutcome,
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
        result = store(
            content=f"test_store_{uuid.uuid4().hex[:8]}",
            source_type="ceo_doc",
            section="test",
            epistemic_status="CONFIRMED",
            deduplicate=False,
        )
        assert result.outcome is StoreOutcome.STORED
        assert result
        assert len(result.id) == 36  # UUID format

    def test_store_deduplication(self):
        unique_content = f"dedup_test_{uuid.uuid4().hex[:8]}"
        id1 = store(content=unique_content, source_type="ceo_doc", deduplicate=True)
        id2 = store(content=unique_content, source_type="ceo_doc", deduplicate=True)
        assert id1
        assert not id2
        assert id2.outcome is StoreOutcome.SKIPPED_DUPLICATE

    def test_store_invalid_source_type_raises(self):
        with pytest.raises(ValueError):
            store(content="test", source_type="invalid_type_xyz")

    def test_store_empty_content_skipped(self):
        result = store(content="", source_type="ceo_doc")
        assert result.outcome is StoreOutcome.SKIPPED_EMPTY
        assert not result
        assert result.id is None

        result2 = store(content="   ", source_type="ceo_doc")
        assert result2.outcome is StoreOutcome.SKIPPED_EMPTY
        assert not result2

    def test_store_duplicate_reports_existing_id(self):
        content = f"duplicate_contract_test_{uuid.uuid4().hex[:8]}"
        first = store(content=content, source_type="ceo_doc")
        assert first.outcome is StoreOutcome.STORED
        assert first

        second = store(content=content, source_type="ceo_doc")
        assert second.outcome is StoreOutcome.SKIPPED_DUPLICATE
        assert not second
        assert second.id is None
        assert second.duplicate_of == first.id

    def test_store_raises_when_insert_returns_no_data(self):
        """Insert accepted but no row back — must raise, never return a skip."""

        class _EmptyResult:
            data = []

        fake_table = MagicMock()
        fake_table.insert.return_value.execute.return_value = _EmptyResult()

        with patch("services.rag_service._get_supabase") as mock_sb, patch(
            "services.rag_service.embed", return_value=[0.0] * 1024
        ):
            mock_sb.return_value.table.return_value = fake_table

            with pytest.raises(RagStoreError) as excinfo:
                store(
                    content="content that must not vanish",
                    source_type="ceo_doc",
                    section="10",
                    deduplicate=False,
                )

        message = str(excinfo.value)
        assert "content that must not vanish" in message
        assert "section=10" in message


class TestBatchStore:
    def test_batch_store_multiple(self):
        chunks = [
            {"content": f"batch_test_item_{i}_{uuid.uuid4().hex[:6]}", "source_type": "ceo_doc"}
            for i in range(5)
        ]
        results = batch_store(chunks)
        assert len(results) == 5
        assert all(r.outcome is StoreOutcome.STORED for r in results)
        assert all(len(r.id) == 36 for r in results)

    def test_batch_store_results_are_positionally_aligned(self):
        """[valid, empty, valid] must map to [STORED, SKIPPED_EMPTY, STORED].

        The old contract dropped skipped chunks entirely, so results[1] was
        the second *valid* chunk's id — a length check alone does not catch it.
        """
        unique = uuid.uuid4().hex[:6]
        chunks = [
            {"content": f"aligned_first_{unique}", "source_type": "ceo_doc"},
            {"content": "   ", "source_type": "ceo_doc"},
            {"content": f"aligned_third_{unique}", "source_type": "ceo_doc"},
        ]
        results = batch_store(chunks)

        assert len(results) == 3
        assert results[0].outcome is StoreOutcome.STORED
        assert results[1].outcome is StoreOutcome.SKIPPED_EMPTY
        assert results[2].outcome is StoreOutcome.STORED
        assert results[1].id is None
        assert results[0].id != results[2].id

    def test_batch_store_marks_invalid_source_type_in_place(self):
        unique = uuid.uuid4().hex[:6]
        chunks = [
            {"content": f"valid_type_{unique}", "source_type": "ceo_doc"},
            {"content": f"bogus_type_{unique}", "source_type": "not_a_real_type"},
        ]
        results = batch_store(chunks)

        assert len(results) == 2
        assert results[0].outcome is StoreOutcome.STORED
        assert results[1].outcome is StoreOutcome.SKIPPED_INVALID_TYPE

    def test_batch_store_reports_failure_not_empty_string(self):
        fake_table = MagicMock()
        fake_table.insert.return_value.execute.side_effect = RuntimeError("boom")

        with patch("services.rag_service._get_supabase") as mock_sb, patch(
            "services.rag_service.embed", return_value=[0.0] * 1024
        ):
            mock_sb.return_value.table.return_value = fake_table
            results = batch_store(
                [{"content": "will not land", "source_type": "ceo_doc"}]
            )

        assert len(results) == 1
        assert results[0].outcome is StoreOutcome.FAILED
        assert not results[0]
        assert "boom" in results[0].error


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
        old_id = store(content=f"old fact {unique}", source_type="ceo_doc", deduplicate=False).id
        new_id = store(content=f"new fact {unique}", source_type="ceo_doc", deduplicate=False).id
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
        ).id
        new_id = store(
            content=f"replacement_{uuid.uuid4().hex[:8]}",
            source_type="ceo_doc",
            deduplicate=False,
        ).id
        result = supersede(old_id, new_id)
        assert result is True
