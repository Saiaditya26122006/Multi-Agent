"""Tests for the non-scope router service.

The queue lives in Supabase (`knowledge_base`), not on local disk — Railway's
filesystem is ephemeral. These tests mock the Supabase client rather than a file.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.non_scope_router import (
    route_to_non_scope,
    get_non_scope_queue,
    get_non_scope_count,
    resolve_non_scope,
)
from services.rag_service import RagStoreError, StoreOutcome, StoreResult


ITEM_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def _row(item_id=ITEM_ID, fact="Some fact", reason="Low confidence", status="pending"):
    return {
        "id": item_id,
        "content": fact,
        "session_id": None,
        "confidence": 0.3,
        "created_at": "2026-07-01T00:00:00Z",
        "metadata": {
            "non_scope": {"status": status, "reason": reason, "confidence": 0.3}
        },
    }


def _queue_supabase(rows):
    """Mock supabase whose queue/count query returns `rows`."""
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value.eq.return_value.contains.return_value.eq.return_value
    chain.order.return_value.execute.return_value = MagicMock(
        data=rows, count=len(rows)
    )
    chain.execute.return_value = MagicMock(data=rows, count=len(rows))
    return sb


class TestRouteToNonScope:
    @patch("services.rag_service.store")
    def test_stores_fact(self, mock_store):
        mock_store.return_value = StoreResult(StoreOutcome.STORED, id=ITEM_ID)

        item_id = route_to_non_scope(
            fact="This doesn't fit anywhere",
            reason="No node match above threshold",
            confidence=0.3,
        )

        assert item_id == ITEM_ID
        kwargs = mock_store.call_args.kwargs
        assert kwargs["content"] == "This doesn't fit anywhere"
        assert kwargs["source_type"] == "ceo_doc"
        assert kwargs["epistemic_status"] == "MISSING"
        assert kwargs["topic_tags"] == ["non-scope", "pending-review"]
        assert kwargs["metadata"]["non_scope"] == {
            "status": "pending",
            "reason": "No node match above threshold",
            "confidence": 0.3,
        }

    @patch("services.rag_service.store")
    def test_duplicate_returns_existing_item(self, mock_store):
        mock_store.return_value = StoreResult(
            StoreOutcome.SKIPPED_DUPLICATE, duplicate_of=ITEM_ID
        )

        assert route_to_non_scope(fact="Already queued", reason="dupe") == ITEM_ID

    @patch("services.rag_service.store")
    def test_store_failure_propagates(self, mock_store):
        """A fact that cannot be queued must not be silently dropped."""
        mock_store.side_effect = RagStoreError("insert returned no data")

        with pytest.raises(RagStoreError):
            route_to_non_scope(fact="Will not land", reason="db down")


class TestGetNonScopeQueue:
    @patch("services.rag_service._get_supabase")
    def test_empty_queue(self, mock_sb):
        mock_sb.return_value = _queue_supabase([])
        assert get_non_scope_queue() == []

    @patch("services.rag_service._get_supabase")
    def test_maps_rows_to_items(self, mock_sb):
        mock_sb.return_value = _queue_supabase([_row(fact="Pricing data")])

        queue = get_non_scope_queue()

        assert len(queue) == 1
        assert queue[0]["id"] == ITEM_ID
        assert queue[0]["fact"] == "Pricing data"
        assert queue[0]["reason"] == "Low confidence"
        assert queue[0]["status"] == "pending"

    @patch("services.rag_service._get_supabase")
    def test_count(self, mock_sb):
        mock_sb.return_value = _queue_supabase([_row(), _row(item_id="other")])
        assert get_non_scope_count() == 2


class TestResolveNonScope:
    def _resolvable_supabase(self, rows):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=rows
        )
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": ITEM_ID}]
        )
        return sb

    @patch("services.non_scope_router._store_resolved_mapping")
    @patch("services.rag_service._get_supabase")
    def test_resolve_map_to_node(self, mock_sb, mock_mapping):
        mock_sb.return_value = self._resolvable_supabase([_row()])

        result = resolve_non_scope(
            item_id=ITEM_ID,
            action="map_to_node",
            target_node="BP.9.1.4",
            reason="Manual review confirms fit",
        )

        assert result["success"] is True
        assert result["action"] == "map_to_node"
        assert result["item"]["status"] == "resolved"
        mock_mapping.assert_called_once()

    @patch("services.non_scope_router._store_resolved_mapping")
    @patch("services.rag_service._get_supabase")
    def test_mapping_failure_leaves_item_pending(self, mock_sb, mock_mapping):
        """The mapping write is the only persistence of a resolved mapping — a
        failure must not quietly drop the item out of the queue."""
        sb = self._resolvable_supabase([_row()])
        mock_sb.return_value = sb
        mock_mapping.side_effect = RagStoreError("insert returned no data")

        result = resolve_non_scope(
            item_id=ITEM_ID, action="map_to_node", target_node="BP.9.1.4"
        )

        assert result["success"] is False
        assert "still in the review queue" in result["error"]
        sb.table.return_value.update.assert_not_called()

    @patch("services.rag_service._get_supabase")
    def test_resolve_discard(self, mock_sb):
        mock_sb.return_value = self._resolvable_supabase([_row()])

        result = resolve_non_scope(
            item_id=ITEM_ID,
            action="discard",
            reason="Not relevant to business plan",
        )

        assert result["success"] is True
        assert result["item"]["resolution"]["action"] == "discard"

    @patch("services.rag_service._get_supabase")
    def test_resolve_invalid_id(self, mock_sb):
        mock_sb.return_value = self._resolvable_supabase([])

        result = resolve_non_scope(item_id="does-not-exist", action="discard")

        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("services.rag_service._get_supabase")
    def test_resolve_already_resolved(self, mock_sb):
        mock_sb.return_value = self._resolvable_supabase([_row(status="resolved")])

        result = resolve_non_scope(item_id=ITEM_ID, action="discard")

        assert result["success"] is False
        assert "already resolved" in result["error"]
