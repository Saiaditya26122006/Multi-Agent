"""
Unit tests for services/temporal_decay.py

These tests run without Supabase — they test pure scoring logic.
"""

import pytest
from datetime import datetime, timedelta, timezone

from services.temporal_decay import (
    is_stale,
    compute_recency_score,
    compute_status_weight,
    compute_final_score,
)


def _iso_ago(days: int) -> str:
    """Helper: return ISO timestamp N days ago."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestIsStale:
    def test_is_stale_true_for_old_data(self):
        created = _iso_ago(200)
        assert is_stale(created, "stale_after_90_days") is True

    def test_is_stale_false_for_fresh_data(self):
        created = _iso_ago(10)
        assert is_stale(created, "stale_after_90_days") is False

    def test_is_stale_uses_last_confirmed(self):
        created = _iso_ago(200)
        confirmed = _iso_ago(5)
        assert is_stale(created, "stale_after_90_days", last_confirmed=confirmed) is False

    def test_never_stale_policy(self):
        created = _iso_ago(9999)
        assert is_stale(created, "never_stale") is False


class TestRecencyScore:
    def test_recency_score_decays(self):
        recent = compute_recency_score(_iso_ago(1))
        old = compute_recency_score(_iso_ago(180))
        assert recent > old

    def test_recency_score_max_is_today(self):
        score = compute_recency_score(_iso_now())
        assert score >= 0.99


class TestStatusWeight:
    def test_status_weight_confirmed_highest(self):
        assert compute_status_weight("CONFIRMED") == 1.0

    def test_status_weight_superseded_lowest(self):
        assert compute_status_weight("SUPERSEDED") == 0.1


class TestFinalScore:
    def test_final_score_combines_all(self):
        score = compute_final_score(
            similarity=0.8,
            created_at=_iso_ago(30),
            epistemic_status="CONFIRMED",
        )
        assert 0.0 <= score <= 1.0

    def test_stale_data_gets_penalized(self):
        fresh_score = compute_final_score(
            similarity=0.8,
            created_at=_iso_ago(10),
            freshness_policy="stale_after_90_days",
            epistemic_status="CONFIRMED",
        )
        stale_score = compute_final_score(
            similarity=0.8,
            created_at=_iso_ago(200),
            freshness_policy="stale_after_90_days",
            epistemic_status="CONFIRMED",
        )
        assert fresh_score > stale_score
