"""Tests for the BUILD workspace handler."""

import pytest
from unittest.mock import patch

from web.handlers.build_handler import (
    build_full_plan,
    build_section,
    build_incremental,
    build_weak_sections,
    get_build_status,
    get_build_blockers,
    format_build_response,
    _normalize_section_id,
)


@pytest.fixture(autouse=True)
def _reset_orchestrator():
    """Clear orchestrator state between tests.

    get_orchestrator() returns a singleton, so a pipeline started by one test stays
    in active_pipelines and makes the next start_build return "Pipeline already
    running".
    """
    from services.pipeline_orchestrator import get_orchestrator

    get_orchestrator().active_pipelines.clear()
    yield
    get_orchestrator().active_pipelines.clear()


class TestBuildFullPlan:
    @patch("web.handlers.build_handler.get_build_blockers", return_value=[])
    @patch("web.handlers.build_handler._get_all_sections", return_value=["BP.1", "BP.2"])
    def test_starts_when_no_blockers(self, mock_sections, mock_blockers):
        result = build_full_plan()
        assert result["status"] == "started"
        assert result["run_id"]

    @patch("web.handlers.build_handler.get_build_blockers")
    def test_blocked_when_dependencies_missing(self, mock_blockers):
        mock_blockers.return_value = [
            {"section_id": "BP.9", "blocked_by": ["BP.5"]}
        ]
        result = build_full_plan()
        assert result["status"] == "blocked"
        assert "1 section(s)" in result["message"]


class TestBuildSection:
    @patch("web.handlers.build_handler._get_section_blockers", return_value=[])
    def test_starts_unblocked_section(self, mock_blockers):
        result = build_section("9")
        assert result["status"] == "started"
        assert result["section"] == "BP.9"

    @patch("web.handlers.build_handler._get_section_blockers", return_value=["BP.5"])
    def test_blocked_section(self, mock_blockers):
        result = build_section("9")
        assert result["status"] == "blocked"
        assert "BP.5" in result["message"]


class TestBuildIncremental:
    @patch("web.handlers.build_handler._get_sections_with_new_data", return_value=["BP.9", "BP.5"])
    def test_rebuilds_sections_with_new_data(self, mock_sections):
        result = build_incremental()
        assert result["status"] == "started"
        assert len(result["sections_to_rebuild"]) == 2

    @patch("web.handlers.build_handler._get_sections_with_new_data", return_value=[])
    def test_nothing_to_build(self, mock_sections):
        result = build_incremental()
        assert result["status"] == "nothing_to_build"


class TestBuildWeakSections:
    @patch("web.handlers.build_handler._get_weak_sections", return_value=["BP.9", "BP.12"])
    def test_finds_weak_sections(self, mock_weak):
        result = build_weak_sections(threshold=40.0)
        assert result["status"] == "started"
        assert len(result["weak_sections"]) == 2

    @patch("web.handlers.build_handler._get_weak_sections", return_value=[])
    def test_all_sections_strong(self, mock_weak):
        result = build_weak_sections(threshold=40.0)
        assert result["status"] == "nothing_to_build"


class TestGetBuildStatus:
    def test_returns_default_status(self):
        status = get_build_status()
        assert status["running"] is False
        assert status["progress"] == 0


class TestNormalizeSectionId:
    def test_plain_number(self):
        assert _normalize_section_id("9") == "BP.9"

    def test_already_prefixed(self):
        assert _normalize_section_id("BP.9") == "BP.9"

    def test_whitespace(self):
        assert _normalize_section_id("  9  ") == "BP.9"


class TestFormatBuildResponse:
    def test_blocked_format(self):
        result = {
            "status": "blocked",
            "message": "Cannot build — 1 section(s) blocked.",
            "blockers": [{"section_id": "BP.9", "blocked_by": ["BP.5"]}],
        }
        text = format_build_response(result)
        assert "blocked" in text.lower() or "Blockers" in text
        assert "BP.9" in text

    def test_started_format(self):
        result = {
            "status": "started",
            "message": "Building section BP.9.",
            "sections_queued": ["BP.9"],
        }
        text = format_build_response(result)
        assert "BP.9" in text
