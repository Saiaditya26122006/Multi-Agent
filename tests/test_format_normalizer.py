"""Tests for the format normalizer service."""

import pytest

from services.format_normalizer import (
    detect_format,
    normalize,
    split_paragraphs,
    split_bullets,
    split_table,
    split_csv,
    split_mixed,
)


class TestDetectFormat:
    def test_bullets(self):
        text = "- First point\n- Second point\n- Third point\n- Fourth"
        assert detect_format(text) == "bullets"

    def test_numbered_bullets(self):
        text = "1. First\n2. Second\n3. Third\n4. Fourth"
        assert detect_format(text) == "bullets"

    def test_pipe_table(self):
        text = "Name|Value|Status\nA|100|Active\nB|200|Inactive"
        assert detect_format(text) == "table"

    def test_tab_table(self):
        text = "Name\tValue\tStatus\nA\t100\tActive\nB\t200\tInactive"
        assert detect_format(text) == "table"

    def test_csv(self):
        text = "name,role,team,status\nAlice,CTO,eng,active\nBob,CEO,exec,active"
        assert detect_format(text) == "csv"

    def test_paragraph(self):
        text = "This is a paragraph. It has multiple sentences. No special format."
        assert detect_format(text) == "paragraph"

    def test_mixed(self):
        text = "Context paragraph one.\nAnother context line.\nA third line.\n- Bullet\n- Bullet two"
        assert detect_format(text) == "mixed"

    def test_empty(self):
        assert detect_format("") == "paragraph"


class TestSplitParagraphs:
    def test_splits_on_sentences(self):
        text = "Revenue comes from SaaS. Pricing is annual. Target is institutions."
        facts = split_paragraphs(text)
        assert len(facts) == 3
        assert facts[0]["source_format"] == "paragraph"

    def test_merges_short_sentences(self):
        text = "Revenue model. It is SaaS-based with annual contracts for institutions."
        facts = split_paragraphs(text)
        assert len(facts) <= 2

    def test_empty_returns_empty(self):
        assert split_paragraphs("") == []


class TestSplitBullets:
    def test_basic_bullets(self):
        text = "- Pricing is per-department\n- Annual contracts\n- SaaS model"
        facts = split_bullets(text)
        assert len(facts) == 3
        assert facts[0]["text"] == "Pricing is per-department"

    def test_star_bullets(self):
        text = "* First pricing point\n* Second revenue model\n* Third target market"
        facts = split_bullets(text)
        assert len(facts) == 3

    def test_filters_short(self):
        text = "- OK\n- This is a valid fact with content"
        facts = split_bullets(text)
        assert len(facts) == 1


class TestSplitTable:
    def test_pipe_table(self):
        text = "Name|Role|Status\n---|---|---\nAlice|CTO|Active\nBob|CEO|Active"
        facts = split_table(text)
        assert len(facts) == 2
        assert "Alice" in facts[0]["text"]
        assert "Name:" in facts[0]["text"] or "Role:" in facts[0]["text"]

    def test_tab_table(self):
        text = "Competitor\tPricing\nIris.ai\tPer-researcher\nSciSpace\tFreemium"
        facts = split_table(text)
        assert len(facts) == 2
        assert "Iris.ai" in facts[0]["text"]


class TestSplitCsv:
    def test_basic_csv(self):
        text = "name,role,status\nAlice,CTO,Active\nBob,CEO,Active"
        facts = split_csv(text)
        assert len(facts) == 2
        assert "name: Alice" in facts[0]["text"]

    def test_quoted_csv(self):
        text = 'name,description\n"Alice","Does engineering"\n"Bob","Does strategy"'
        facts = split_csv(text)
        assert len(facts) == 2


class TestSplitMixed:
    def test_bullets_and_paragraphs(self):
        text = "Some context about pricing.\n- SaaS model\n- Annual contracts\nMore context."
        facts = split_mixed(text)
        assert any("SaaS model" in f["text"] for f in facts)
        assert any(f["source_format"] == "mixed_bullet" for f in facts)


class TestNormalize:
    def test_normalizes_bullets(self):
        text = "- Fact one\n- Fact two\n- Fact three\n- Fact four"
        facts = normalize(text)
        assert len(facts) == 4

    def test_normalizes_paragraph(self):
        text = "This is a sentence. And another sentence. Plus a third one."
        facts = normalize(text)
        assert len(facts) >= 2

    def test_empty_returns_empty(self):
        assert normalize("") == []
        assert normalize("   ") == []
