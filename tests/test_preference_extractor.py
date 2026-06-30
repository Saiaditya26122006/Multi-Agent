"""
Tests for services/preference_extractor.py
"""

import pytest
from unittest.mock import patch, MagicMock

from services.preference_extractor import (
    extract_patterns,
    _detect_theme,
)
from services.rag_service import Chunk


class TestDetectTheme:
    def test_no_patterns_with_few_decisions(self):
        texts = ["rejected because too optimistic"]
        result = _detect_theme(texts, "rejection")
        assert result is None

    def test_detects_optimistic_rejection_pattern(self):
        texts = [
            "rejected — projections too optimistic",
            "killed because numbers are optimistic and unrealistic",
            "adjust — make it less optimistic, more conservative",
            "feedback: this is way too optimistic for the market",
        ]
        result = _detect_theme(texts, "rejection")
        assert result is not None
        assert result["theme"] == "optimistic"
        assert result["count"] >= 3

    def test_confidence_increases_with_count(self):
        texts_few = [
            "too optimistic",
            "way too optimistic",
            "unrealistically optimistic",
            "not great but acceptable",
            "could be better honestly",
            "the market analysis was weak",
            "needs more conservative approach",
            "projections seem inflated overall",
        ]
        texts_many = [
            "too optimistic",
            "way too optimistic",
            "unrealistically optimistic",
            "numbers are optimistic",
            "still optimistic",
            "again optimistic projections",
        ]
        result_few = _detect_theme(texts_few, "rejection")
        result_many = _detect_theme(texts_many, "rejection")
        assert result_many["confidence"] >= result_few["confidence"]
