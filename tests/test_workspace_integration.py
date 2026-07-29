"""Integration tests for the workspace system end-to-end flow.

Verifies the full internal dispatch and handler pipeline with external
services (Redis, Supabase, LLM) mocked out.
"""

import pytest
from unittest.mock import patch, MagicMock

from services.rag_service import StoreOutcome, StoreResult


class TestFeedWorkspaceFlow:
    """Test raw text -> workspace_router -> feed_handler -> mapped facts."""

    def test_raw_text_produces_mapped_facts(self):
        from web.handlers.feed_handler import handle_raw_text

        result = handle_raw_text("Our pricing is per-department SaaS bundles")

        assert result["count"] > 0
        assert len(result["facts"]) > 0
        assert result["format_detected"] == "paragraph"

    def test_raw_text_bullets_parsed(self):
        from web.handlers.feed_handler import handle_raw_text

        result = handle_raw_text(
            "- Pricing is SaaS\n- Target is universities\n- Revenue from subscriptions"
        )

        assert result["format_detected"] == "bullets"
        assert result["count"] == 3

    @patch("web.workspace_router._get_redis_client")
    def test_full_dispatch_to_feed_returns_facts(self, mock_redis):
        """End-to-end: dispatch routes to feed, feed_handler processes, response returned."""
        mock_r = MagicMock()
        mock_r.get.return_value = "feed"
        mock_redis.return_value = mock_r

        from web.workspace_router import dispatch
        from web.handlers.feed_handler import handle_raw_text, format_feed_response

        dispatch_result = dispatch("session-e2e", "- Revenue from SaaS\n- Annual contracts\n- We assume 15% churn")
        assert dispatch_result["action"] == "handle_message"

        message = dispatch_result["data"]["message"]
        feed_result = handle_raw_text(message)
        response = format_feed_response(feed_result)

        assert feed_result["count"] == 3
        assert "Extracted 3 fact(s)" in response
        assert "ASSUMPTION" in response

    def test_epistemic_tagging_in_feed_output(self):
        """Feed handler correctly tags epistemic status from language cues."""
        from web.handlers.feed_handler import handle_raw_text

        result = handle_raw_text(
            "- We confirmed annual pricing with 3 universities\n"
            "- I think doctoral programs might need different pricing\n"
            "- The contract states 12-month minimum"
        )

        facts = result["facts"]
        confirmed = [f for f in facts if f["inferred_status"] == "CONFIRMED"]
        assumptions = [f for f in facts if f["inferred_status"] == "ASSUMPTION"]

        assert len(confirmed) >= 1
        assert len(assumptions) >= 1

    @patch("services.rag_service.retrieve")
    def test_correction_detected_from_similar_existing(self, mock_retrieve):
        mock_chunk = MagicMock()
        mock_chunk.id = "old_1"
        mock_chunk.content = "Pricing is per-seat"
        mock_chunk.similarity = 0.92
        mock_retrieve.return_value = [mock_chunk]

        from web.handlers.feed_handler import detect_corrections

        result = detect_corrections("Pricing is per-department, not per-seat")

        assert result is not None
        assert result["type"] == "potential_correction"
        assert result["existing_chunk_id"] == "old_1"

    @patch("services.rag_service.retrieve")
    def test_no_correction_when_no_match(self, mock_retrieve):
        mock_retrieve.return_value = []

        from web.handlers.feed_handler import detect_corrections

        result = detect_corrections("Brand new information about nothing")
        assert result is None


class TestCorrectionSupersedes:
    """Test: correction sent -> old fact superseded via conversation_store."""

    @patch("services.rag_service.supersede")
    @patch("services.rag_service.store")
    def test_correction_stores_new_and_supersedes_old(self, mock_store, mock_supersede):
        """Correction creates new chunk and marks old one as superseded."""
        mock_store.return_value = StoreResult(StoreOutcome.STORED, id="new-uuid-456")

        from services.conversation_store import store_correction

        result = store_correction(
            original_fact="Revenue target is $500K ARR",
            corrected_fact="Revenue target is $750K ARR",
            session_id="session-xyz",
            original_chunk_id="old-uuid-123",
        )

        assert result == "new-uuid-456"
        mock_store.assert_called_once()
        mock_supersede.assert_called_once_with("old-uuid-123", "new-uuid-456")

    @patch("services.rag_service.supersede")
    @patch("services.rag_service.store")
    def test_correction_duplicate_still_supersedes(self, mock_store, mock_supersede):
        """A duplicate correction must not leave the stale chunk authoritative."""
        mock_store.return_value = StoreResult(
            StoreOutcome.SKIPPED_DUPLICATE, duplicate_of="existing-uuid-999"
        )

        from services.conversation_store import store_correction

        result = store_correction(
            original_fact="Revenue target is $500K ARR",
            corrected_fact="Revenue target is $750K ARR",
            original_chunk_id="old-uuid-123",
        )

        assert result == "existing-uuid-999"
        mock_supersede.assert_called_once_with("old-uuid-123", "existing-uuid-999")

    @patch("services.rag_service.store")
    def test_correction_without_original_id_only_stores(self, mock_store):
        """When original_chunk_id is not provided, store without supersede."""
        mock_store.return_value = StoreResult(StoreOutcome.STORED, id="new-uuid-789")

        from services.conversation_store import store_correction

        result = store_correction(
            original_fact="Market is Spain only",
            corrected_fact="Market is Spain + Portugal",
            session_id="session-xyz",
        )

        assert result == "new-uuid-789"

    @patch("services.rag_service.store")
    def test_correction_content_includes_both_facts(self, mock_store):
        """Stored correction content documents what changed."""
        mock_store.return_value = StoreResult(StoreOutcome.STORED, id="uuid-1")

        from services.conversation_store import store_correction

        store_correction(
            original_fact="Old pricing model",
            corrected_fact="New pricing model",
        )

        call_kwargs = mock_store.call_args
        content = call_kwargs[1]["content"] if call_kwargs[1] else call_kwargs[0][0]
        assert "Old pricing model" in content
        assert "New pricing model" in content


class TestWorkspaceRouting:
    """Test workspace switching and routing."""

    @patch("web.workspace_router._get_redis_client")
    def test_workspace_switch_persists(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "feed"
        mock_redis.return_value = mock_r

        from web.workspace_router import set_workspace, Workspace

        set_workspace("session_1", Workspace.FEED)
        mock_r.set.assert_called_once()

    @patch("web.workspace_router._get_redis_client")
    def test_workspace_switch_preserves_session_state(self, mock_redis):
        """After set_workspace, get_workspace returns the new value."""
        store = {}

        def fake_set(key, value, ex=None):
            store[key] = value

        def fake_get(key):
            return store.get(key)

        mock_r = MagicMock()
        mock_r.set = fake_set
        mock_r.get = fake_get
        mock_redis.return_value = mock_r

        from web.workspace_router import set_workspace, get_workspace, Workspace

        set_workspace("session_state", Workspace.BUILD)
        assert get_workspace("session_state") == Workspace.BUILD

        set_workspace("session_state", Workspace.CHALLENGE)
        assert get_workspace("session_state") == Workspace.CHALLENGE

    @patch("web.workspace_router._get_redis_client")
    def test_multiple_sessions_independent(self, mock_redis):
        """Different sessions maintain independent workspace state."""
        store = {}

        def fake_set(key, value, ex=None):
            store[key] = value

        def fake_get(key):
            return store.get(key)

        mock_r = MagicMock()
        mock_r.set = fake_set
        mock_r.get = fake_get
        mock_redis.return_value = mock_r

        from web.workspace_router import set_workspace, get_workspace, Workspace

        set_workspace("session-A", Workspace.FEED)
        set_workspace("session-B", Workspace.EXPORT)

        assert get_workspace("session-A") == Workspace.FEED
        assert get_workspace("session-B") == Workspace.EXPORT

    @patch("web.workspace_router._get_redis_client")
    def test_menu_command_returns_menu(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "feed"
        mock_redis.return_value = mock_r

        from web.workspace_router import dispatch

        result = dispatch("session_1", "menu")
        assert result["action"] == "show_menu"

    @patch("web.workspace_router._get_redis_client")
    def test_number_input_switches_workspace(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "auto"
        mock_redis.return_value = mock_r

        from web.workspace_router import dispatch

        result = dispatch("session_1", "1")
        assert result["action"] == "switch_workspace"

    @patch("web.workspace_router._get_redis_client")
    def test_back_returns_to_menu(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "feed"
        mock_redis.return_value = mock_r

        from web.workspace_router import dispatch

        result = dispatch("session_1", "back")
        assert result["action"] == "show_menu"

    @patch("web.workspace_router._get_redis_client")
    def test_regular_message_dispatched_to_handler(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "build"
        mock_redis.return_value = mock_r

        from web.workspace_router import dispatch

        result = dispatch("session_1", "Generate section 9")
        assert result["action"] == "handle_message"
        assert result["data"]["message"] == "Generate section 9"


class TestMenuCommandWithStats:
    """Test: 'menu' command returns formatted menu with live stats."""

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_dashboard_stats")
    @patch("services.recommendation_engine.get_highest_leverage_action")
    def test_menu_includes_all_workspaces(self, mock_action, mock_stats, mock_blocked):
        """Generated menu has 7 workspace items."""
        mock_stats.return_value = {
            "total_nodes": 20,
            "coverage_pct": 35.0,
            "confidence_pct": 60.0,
            "confirmed_count": 12,
            "total_tagged": 20,
            "contradiction_count": 2,
            "stale_count": 4,
            "oldest_assumption_age_days": 25,
        }
        mock_action.return_value = {
            "action_text": "Resolve 2 contradictions",
            "workspace": "challenge",
            "priority": "medium",
            "reasoning": "Contradictions block progress",
        }
        mock_blocked.return_value = []

        from web.menu_generator import generate_main_menu

        menu = generate_main_menu()
        assert len(menu["items"]) == 7
        assert menu["dashboard"]["coverage_pct"] == 35.0
        assert menu["recommendation"]["workspace"] == "challenge"

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_dashboard_stats")
    @patch("services.recommendation_engine.get_highest_leverage_action")
    def test_menu_text_contains_stats(self, mock_action, mock_stats, mock_blocked):
        """Formatted menu text includes live coverage and contradiction counts."""
        mock_stats.return_value = {
            "total_nodes": 20,
            "coverage_pct": 42.0,
            "confidence_pct": 55.0,
            "confirmed_count": 11,
            "total_tagged": 20,
            "contradiction_count": 3,
            "stale_count": 2,
            "oldest_assumption_age_days": 10,
        }
        mock_action.return_value = {
            "action_text": "Feed data",
            "workspace": "feed",
            "priority": "low",
            "reasoning": "",
        }
        mock_blocked.return_value = []

        from web.menu_generator import generate_main_menu, format_menu_as_text

        menu = generate_main_menu()
        text = format_menu_as_text(menu)
        assert "Coverage: 42%" in text
        assert "Confidence: 55%" in text
        assert "Contradictions: 3" in text

    @patch("web.workspace_router._get_redis_client")
    def test_number_1_input_switches_to_feed(self, mock_redis):
        """Input '1' switches to FEED workspace with correct label."""
        store = {}

        def fake_set(key, value, ex=None):
            store[key] = value

        mock_r = MagicMock()
        mock_r.set = fake_set
        mock_redis.return_value = mock_r

        from web.workspace_router import dispatch, Workspace

        result = dispatch("session-num", "1")
        assert result["action"] == "switch_workspace"
        assert result["workspace"] == Workspace.FEED
        assert result["data"]["label"] == "Feed Data"


class TestInspectWorkspace:
    """Test INSPECT workspace queries."""

    @patch("services.coverage_calculator.get_plan_coverage")
    def test_coverage_heatmap_returns_sections(self, mock_coverage):
        mock_coverage.return_value = {
            "coverage_pct": 45.0,
            "total_nodes": 100,
            "filled_nodes": 45,
            "per_section": {
                "BP.1": {
                    "section_id": "BP.1",
                    "title": "Business Model",
                    "total_nodes": 20,
                    "filled_nodes": 16,
                    "coverage_pct": 80.0,
                },
                "BP.5": {
                    "section_id": "BP.5",
                    "title": "SWOT",
                    "total_nodes": 15,
                    "filled_nodes": 9,
                    "coverage_pct": 60.0,
                },
                "BP.9": {
                    "section_id": "BP.9",
                    "title": "Operations",
                    "total_nodes": 10,
                    "filled_nodes": 3,
                    "coverage_pct": 30.0,
                },
            },
        }

        from web.handlers.inspect_handler import get_coverage_heatmap

        result = get_coverage_heatmap()

        assert "sections" in result
        assert len(result["sections"]) == 3
        assert result["sections"][0]["status"] == "strong"
        assert result["sections"][1]["status"] == "moderate"
        assert result["sections"][2]["status"] == "weak"


class TestNonScopeRouting:
    """Test non-scope facts appear in queue (backed by Supabase, not disk)."""

    @patch("services.rag_service._get_supabase")
    def test_non_scope_items_in_queue(self, mock_sb):
        row = {
            "id": "8f14e45f-ceea-467a-9f1a-1f0f1f0f1f0f",
            "content": "Random unrelated thing",
            "session_id": None,
            "confidence": 0.2,
            "created_at": "2026-07-01T00:00:00Z",
            "metadata": {
                "non_scope": {
                    "status": "pending",
                    "reason": "no_match",
                    "confidence": 0.2,
                }
            },
        }
        query = MagicMock()
        query.execute.return_value = MagicMock(data=[row])
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.contains.return_value.eq.return_value.order.return_value = (
            query
        )

        from services.non_scope_router import get_non_scope_queue

        queue = get_non_scope_queue()

        assert len(queue) == 1
        assert queue[0]["fact"] == "Random unrelated thing"
        assert queue[0]["reason"] == "no_match"
        assert queue[0]["id"] == row["id"]

    @patch("services.rag_service.store")
    def test_route_to_non_scope_returns_id(self, mock_store):
        from services.rag_service import StoreOutcome, StoreResult

        chunk_id = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        mock_store.return_value = StoreResult(StoreOutcome.STORED, id=chunk_id)

        from services.non_scope_router import route_to_non_scope

        result = route_to_non_scope(
            "The weather in Barcelona is lovely",
            "no_matching_node",
        )

        assert result == chunk_id
        kwargs = mock_store.call_args.kwargs
        assert kwargs["epistemic_status"] == "MISSING"
        assert "non-scope" in kwargs["topic_tags"]
        assert kwargs["metadata"]["non_scope"]["status"] == "pending"


class TestAutoModeRouting:
    """Test AUTO mode intent classification."""

    def test_question_classified_correctly(self):
        from web.handlers.auto_handler import classify_intent

        result = classify_intent("What's the status of section 9?")
        assert result["intent"] == "question"

    def test_data_input_long_text_classified(self):
        from web.handlers.auto_handler import classify_intent

        long_text = (
            "Our target market is European universities with research departments "
            "that have budget authority. The typical buyer persona is a department "
            "head or research dean with procurement power."
        )
        result = classify_intent(long_text)
        assert result["intent"] == "new_data"

    def test_decision_classified_correctly(self):
        from web.handlers.auto_handler import classify_intent

        result = classify_intent("Yes, approve that")
        assert result["intent"] == "decision"

    def test_command_classified_correctly(self):
        from web.handlers.auto_handler import classify_intent

        result = classify_intent("Generate section 9 now")
        assert result["intent"] == "command"

    def test_correction_classified_correctly(self):
        from web.handlers.auto_handler import classify_intent

        result = classify_intent("Actually, the pricing is per-department not per-seat")
        assert result["intent"] == "correction"
