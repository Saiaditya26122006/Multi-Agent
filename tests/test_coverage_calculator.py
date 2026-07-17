"""Tests for the coverage calculator service."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


class TestGetTotalNodeCount:
    """Test total node count from architecture."""

    @patch("services.coverage_calculator._load_bp_architecture")
    def test_returns_correct_count(self, mock_arch):
        mock_arch.return_value = {"nodes": [{"node_id": "BP.1"}, {"node_id": "BP.2"}]}
        from services.coverage_calculator import get_total_node_count

        assert get_total_node_count() == 2

    @patch("services.coverage_calculator._load_bp_architecture")
    def test_empty_nodes(self, mock_arch):
        mock_arch.return_value = {"nodes": []}
        from services.coverage_calculator import get_total_node_count

        # Reset the cache to force reload
        import services.coverage_calculator as cc
        cc._bp_architecture = None
        mock_arch.return_value = {"nodes": []}
        assert get_total_node_count() == 0


class TestGetSections:
    """Test section metadata extraction."""

    @patch("services.coverage_calculator._load_bp_architecture")
    def test_groups_nodes_by_section(self, mock_arch):
        mock_arch.return_value = {
            "nodes": [
                {"node_id": "BP.1", "node_title": "Product Definition"},
                {"node_id": "BP.1.1", "node_title": "Sub-product"},
                {"node_id": "BP.2", "node_title": "Market"},
            ]
        }
        from services.coverage_calculator import get_sections

        import services.coverage_calculator as cc
        cc._bp_architecture = None

        sections = get_sections()
        assert "BP.1" in sections
        assert "BP.2" in sections
        assert sections["BP.1"]["node_count"] == 2
        assert sections["BP.2"]["node_count"] == 1
        assert sections["BP.1"]["title"] == "Product Definition"

    @patch("services.coverage_calculator._load_bp_architecture")
    def test_empty_architecture(self, mock_arch):
        mock_arch.return_value = {"nodes": []}
        from services.coverage_calculator import get_sections

        import services.coverage_calculator as cc
        cc._bp_architecture = None

        sections = get_sections()
        assert sections == {}


class TestGetPlanCoverage:
    """Test plan coverage calculation."""

    # get_plan_coverage resolves filled nodes via _get_filled_node_ids (one Supabase
    # query), not per-node rag_service.retrieve calls. Patching retrieve here left
    # the mock dead and sent these tests at the live database.

    @patch("services.coverage_calculator._get_filled_node_ids")
    @patch("services.coverage_calculator._load_bp_architecture")
    def test_full_coverage_when_all_nodes_have_data(self, mock_arch, mock_filled):
        mock_arch.return_value = {
            "nodes": [
                {"node_id": "BP.1", "node_title": "Product"},
                {"node_id": "BP.1.1", "node_title": "Identity"},
            ]
        }
        mock_filled.return_value = {"BP.1", "BP.1.1"}

        import services.coverage_calculator as cc
        cc._bp_architecture = None

        from services.coverage_calculator import get_plan_coverage

        result = get_plan_coverage()
        assert result["total_nodes"] == 2
        assert result["filled_nodes"] == 2
        assert result["coverage_pct"] == 100.0

    @patch("services.coverage_calculator._get_filled_node_ids")
    @patch("services.coverage_calculator._load_bp_architecture")
    def test_zero_coverage_when_no_data(self, mock_arch, mock_filled):
        mock_arch.return_value = {
            "nodes": [
                {"node_id": "BP.1", "node_title": "Product"},
                {"node_id": "BP.2", "node_title": "Market"},
            ]
        }
        mock_filled.return_value = set()

        import services.coverage_calculator as cc
        cc._bp_architecture = None

        from services.coverage_calculator import get_plan_coverage

        result = get_plan_coverage()
        assert result["total_nodes"] == 2
        assert result["filled_nodes"] == 0
        assert result["coverage_pct"] == 0.0

    @patch("services.coverage_calculator._load_bp_architecture")
    def test_returns_fallback_on_error(self, mock_arch):
        mock_arch.return_value = {"nodes": [{"node_id": "BP.1"}]}

        import services.coverage_calculator as cc
        cc._bp_architecture = None

        with patch(
            "services.coverage_calculator._get_filled_node_ids",
            side_effect=Exception("DB down"),
        ):
            from services.coverage_calculator import get_plan_coverage

            result = get_plan_coverage()
            assert result["coverage_pct"] == 0.0
            assert result["filled_nodes"] == 0


class TestGetConfidenceBreakdown:
    """Test epistemic confidence breakdown."""

    @patch("services.rag_service._get_supabase")
    def test_calculates_confidence_pct(self, mock_supabase):
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.not_.is_.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            {"epistemic_status": "CONFIRMED"},
            {"epistemic_status": "CONFIRMED"},
            {"epistemic_status": "ASSUMPTION"},
            {"epistemic_status": "INFERRED"},
        ])

        from services.coverage_calculator import get_confidence_breakdown

        result = get_confidence_breakdown()
        assert result["total_tagged"] == 4
        assert result["confirmed_count"] == 2
        assert result["confidence_pct"] == 50.0
        assert result["breakdown"]["CONFIRMED"] == 2
        assert result["breakdown"]["ASSUMPTION"] == 1

    @patch("services.rag_service._get_supabase")
    def test_handles_empty_data(self, mock_supabase):
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.not_.return_value = mock_table
        mock_table.is_.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])

        from services.coverage_calculator import get_confidence_breakdown

        result = get_confidence_breakdown()
        assert result["total_tagged"] == 0
        assert result["confidence_pct"] == 0.0

    def test_handles_exception_gracefully(self):
        with patch(
            "services.rag_service._get_supabase",
            side_effect=Exception("Connection failed"),
        ):
            from services.coverage_calculator import get_confidence_breakdown

            result = get_confidence_breakdown()
            assert result["confidence_pct"] == 0.0
            assert result["breakdown"] == {}


def _mock_supabase_rows(rows):
    """Stub the supabase query chain get_contradiction_count builds."""
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value.eq.return_value.is_.return_value
    chain.execute.return_value = MagicMock(data=rows)
    return sb


class TestGetContradictionCount:
    """Test contradiction counting.

    Unresolved-ness is now decided by the Supabase query itself
    (epistemic_status == "CONTRADICTION" AND superseded_by IS NULL). These tests
    used to stub rag_service.retrieve and filter on a metadata["resolved"] flag —
    a flag nothing ever sets, which made every past resolution count as a new open
    issue. That behaviour was deliberately removed.
    """

    @patch("services.rag_service._get_supabase")
    def test_counts_unresolved_contradictions(self, mock_sb):
        mock_sb.return_value = _mock_supabase_rows([{"id": "a"}, {"id": "b"}])

        from services.coverage_calculator import get_contradiction_count

        assert get_contradiction_count() == 2

    @patch("services.rag_service._get_supabase")
    def test_zero_when_all_resolved(self, mock_sb):
        # Resolved contradictions carry superseded_by, so the query excludes them.
        mock_sb.return_value = _mock_supabase_rows([])

        from services.coverage_calculator import get_contradiction_count

        assert get_contradiction_count() == 0

    @patch("services.rag_service._get_supabase")
    def test_zero_on_empty_result(self, mock_sb):
        mock_sb.return_value = _mock_supabase_rows([])

        from services.coverage_calculator import get_contradiction_count

        assert get_contradiction_count() == 0

    def test_returns_zero_on_error(self):
        with patch(
            "services.rag_service._get_supabase",
            side_effect=Exception("Unavailable"),
        ):
            from services.coverage_calculator import get_contradiction_count

            assert get_contradiction_count() == 0


class TestGetStaleItems:
    """Test stale item detection."""

    @patch("services.rag_service._get_supabase")
    def test_finds_items_older_than_threshold(self, mock_supabase):
        old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.lt.return_value = mock_table
        mock_table.is_.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            {
                "id": "uuid-1",
                "content": "Old fact about market sizing",
                "source_type": "ceo_doc",
                "created_at": old_date,
            }
        ])

        from services.coverage_calculator import get_stale_items

        stale = get_stale_items(max_age_days=30)
        assert len(stale) == 1
        assert stale[0]["id"] == "uuid-1"
        assert stale[0]["age_days"] >= 45
        assert stale[0]["source_type"] == "ceo_doc"

    @patch("services.rag_service._get_supabase")
    def test_empty_result_returns_empty_list(self, mock_supabase):
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.lt.return_value = mock_table
        mock_table.is_.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])

        from services.coverage_calculator import get_stale_items

        assert get_stale_items() == []

    def test_returns_empty_on_error(self):
        with patch(
            "services.rag_service._get_supabase",
            side_effect=Exception("DB timeout"),
        ):
            from services.coverage_calculator import get_stale_items

            assert get_stale_items() == []


class TestGetOldestAssumptions:
    """Test oldest unvalidated assumption retrieval."""

    @patch("services.rag_service._get_supabase")
    def test_returns_oldest_first(self, mock_supabase):
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.is_.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            {
                "id": "uuid-oldest",
                "content": "We assume institutional pricing will work",
                "created_at": old_date,
            }
        ])

        from services.coverage_calculator import get_oldest_assumptions

        result = get_oldest_assumptions(top_k=3)
        assert len(result) == 1
        assert result[0]["id"] == "uuid-oldest"
        assert result[0]["age_days"] >= 60

    @patch("services.rag_service._get_supabase")
    def test_respects_top_k(self, mock_supabase):
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.is_.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])

        from services.coverage_calculator import get_oldest_assumptions

        result = get_oldest_assumptions(top_k=1)
        mock_table.limit.assert_called_with(1)
        assert result == []


class TestGetBlockedSections:
    """Test blocked section detection via dependency graph."""

    @patch("services.rag_service._get_supabase")
    @patch("services.coverage_calculator._load_bp_dependencies")
    def test_detects_blocked_sections(self, mock_deps, mock_supabase):
        mock_deps.return_value = {
            "dependencies": {
                "BP.5.1": ["BP.1.1", "BP.3.1"],
            }
        }
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.not_.is_.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            {"section": "1"},
        ])

        import services.coverage_calculator as cc
        cc._bp_dependencies = None

        from services.coverage_calculator import get_blocked_sections

        blocked = get_blocked_sections()
        assert len(blocked) >= 1
        blocked_ids = [b["section_id"] for b in blocked]
        assert "BP.5" in blocked_ids

    @patch("services.rag_service._get_supabase")
    @patch("services.coverage_calculator._load_bp_dependencies")
    def test_no_blocked_when_all_deps_met(self, mock_deps, mock_supabase):
        mock_deps.return_value = {
            "dependencies": {
                "BP.2.1": ["BP.1.1"],
            }
        }
        mock_table = MagicMock()
        mock_supabase.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.not_.is_.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            {"section": "1"},
        ])

        import services.coverage_calculator as cc
        cc._bp_dependencies = None

        from services.coverage_calculator import get_blocked_sections

        blocked = get_blocked_sections()
        assert isinstance(blocked, list)


class TestGetDashboardStats:
    """Test the aggregate dashboard stats function."""

    @patch("services.coverage_calculator.get_oldest_assumptions")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_confidence_breakdown")
    @patch("services.coverage_calculator._load_bp_architecture")
    def test_aggregates_all_stats(
        self, mock_arch, mock_confidence, mock_contradictions, mock_stale, mock_oldest
    ):
        mock_arch.return_value = {"nodes": [{"node_id": "BP.1"}, {"node_id": "BP.2"}]}
        mock_confidence.return_value = {
            "confidence_pct": 75.0,
            "confirmed_count": 3,
            "total_tagged": 4,
        }
        mock_contradictions.return_value = 2
        mock_stale.return_value = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
        mock_oldest.return_value = [{"age_days": 42}]

        import services.coverage_calculator as cc
        cc._bp_architecture = None
        cc._dashboard_cache["stats"] = None
        cc._dashboard_cache["timestamp"] = 0.0

        from services.coverage_calculator import get_dashboard_stats

        stats = get_dashboard_stats()
        assert stats["total_nodes"] == 2
        assert stats["confidence_pct"] == 75.0
        assert stats["contradiction_count"] == 2
        assert stats["stale_count"] == 3
        assert stats["oldest_assumption_age_days"] == 42

    @patch("services.coverage_calculator.get_confidence_breakdown")
    @patch("services.coverage_calculator._load_bp_architecture")
    def test_returns_defaults_on_error(self, mock_arch, mock_confidence):
        mock_arch.side_effect = Exception("File not found")

        import services.coverage_calculator as cc
        cc._bp_architecture = None
        cc._dashboard_cache["stats"] = None
        cc._dashboard_cache["timestamp"] = 0.0

        from services.coverage_calculator import get_dashboard_stats

        stats = get_dashboard_stats()
        assert stats["total_nodes"] == 0
        assert stats["coverage_pct"] == 0.0
        assert stats["contradiction_count"] == 0
