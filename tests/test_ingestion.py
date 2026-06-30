"""
Unit tests for services/ingestion_pipeline.py

Tests chunking logic without requiring Supabase (mocks batch_store).
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from services.ingestion_pipeline import (
    _chunk_customers,
    _chunk_contradictions,
    _chunk_bp_architecture,
    _chunk_prohibited_claims,
    _chunk_capabilities,
    _normalize_status,
    _chunk_generic,
    ingest_all_ceo_data,
)


CEO_DATA_DIR = Path(__file__).parent.parent / "ceo_data"


def _load_json(filename: str) -> dict:
    path = CEO_DATA_DIR / filename
    with open(path, "r") as f:
        return json.load(f)


class TestChunkCustomers:
    def test_chunk_customers_produces_correct_count(self):
        data = _load_json("customers.json")
        chunks = _chunk_customers(data, "3", ["customers"])
        assert len(chunks) >= 5


class TestChunkContradictions:
    def test_chunk_contradictions_produces_9(self):
        data = _load_json("contradictions.json")
        chunks = _chunk_contradictions(data, "18", ["contradictions"])
        assert len(chunks) == 9


class TestChunkPreservesStatus:
    def test_chunk_preserves_epistemic_status(self):
        data = _load_json("contradictions.json")
        chunks = _chunk_contradictions(data, "18", ["contradictions"])
        statuses = [c.get("epistemic_status") for c in chunks]
        assert "CONTRADICTION" in statuses


class TestChunkBPArchitecture:
    def test_chunk_bp_architecture_produces_20(self):
        data = _load_json("bp_architecture.json")
        chunks = _chunk_bp_architecture(data, "governance", ["architecture"])
        assert len(chunks) == 20


class TestChunkProhibitedClaims:
    def test_chunk_prohibited_claims_has_global(self):
        data = _load_json("prohibited_claims.json")
        chunks = _chunk_prohibited_claims(data, "governance", ["constraints"])
        contents = [c["content"] for c in chunks]
        global_chunks = [c for c in contents if "GLOBAL PROHIBITION" in c]
        assert len(global_chunks) >= 3


class TestNormalizeStatus:
    def test_normalize_status_handles_variants(self):
        assert _normalize_status("CONFIRMED") == "CONFIRMED"
        assert _normalize_status("CONFIRMED as internal ICP hypothesis") == "CONFIRMED"
        assert _normalize_status("ASSUMPTION") == "ASSUMPTION"
        assert _normalize_status("MISSING") == "MISSING"
        assert _normalize_status("REQUIRED") == "MISSING"
        assert _normalize_status("UNVERIFIED EXTERNAL CLAIM") == "UNVERIFIED_EXTERNAL_CLAIM"
        assert _normalize_status(None) is None


class TestEmptyFileProducesGap:
    def test_empty_file_produces_gap_chunk(self):
        data = _load_json("team.json")
        chunks = _chunk_generic(data, "4", ["team"])
        assert len(chunks) >= 1
        assert any(c.get("epistemic_status") == "MISSING" for c in chunks)


class TestIngestAllCount:
    @patch("services.rag_service.batch_store")
    def test_ingest_all_ceo_data_count(self, mock_batch_store):
        mock_batch_store.return_value = ["fake-id"] * 50
        result = ingest_all_ceo_data()
        total = result.get("_total", 0)
        assert total > 100
