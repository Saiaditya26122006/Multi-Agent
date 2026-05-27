"""
Learning Engine — feedback loop for Phase 2 pipeline.

Stores patterns from CEO feedback:
- What was accepted vs rejected
- Which sections Alex edited (implies weakness)
- Which Devil's Advocate challenges were valid vs dismissed
- Pattern memory across runs to improve future outputs
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from memory.redis_client import RedisClient

logger = logging.getLogger(__name__)

PATTERN_KEY_PREFIX = "learning:pattern"
FEEDBACK_KEY_PREFIX = "learning:feedback"
REJECTION_KEY_PREFIX = "learning:rejection"


class LearningEngine:
    """Accumulates CEO feedback and applies it to future runs."""

    def __init__(self, redis_client: RedisClient, supabase_client=None):
        self.redis = redis_client
        self.supabase = supabase_client

    def record_acceptance(
        self,
        session_id: str,
        section_number: str,
        confidence_score: str,
        assumptions_count: int,
        devils_advocate_verdict: str,
    ) -> None:
        """Record that a section was accepted by the CEO without edits."""
        pattern = {
            "event": "accepted",
            "section": section_number,
            "confidence": confidence_score,
            "assumptions_count": assumptions_count,
            "da_verdict": devils_advocate_verdict,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
        }
        key = f"{PATTERN_KEY_PREFIX}:{session_id}:{section_number}"
        self.redis.client.set(key, json.dumps(pattern), ex=86400 * 30)
        logger.info("[LearningEngine] Recorded acceptance: section %s", section_number)

    def record_rejection(
        self,
        session_id: str,
        section_number: str,
        reason: str,
        ceo_feedback: str,
    ) -> None:
        """Record that a section was rejected or heavily edited by the CEO."""
        rejection = {
            "event": "rejected",
            "section": section_number,
            "reason": reason,
            "ceo_feedback": ceo_feedback,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
        }
        key = f"{REJECTION_KEY_PREFIX}:{session_id}:{section_number}"
        self.redis.client.set(key, json.dumps(rejection), ex=86400 * 30)
        logger.info("[LearningEngine] Recorded rejection: section %s — %s", section_number, reason)

    def record_edit(
        self,
        session_id: str,
        section_number: str,
        field_edited: str,
        original_value: str,
        new_value: str,
    ) -> None:
        """Record a CEO edit to a specific field — implies the agent got it wrong."""
        feedback = {
            "event": "edit",
            "section": section_number,
            "field": field_edited,
            "original": original_value[:500],
            "corrected": new_value[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
        }
        key = f"{FEEDBACK_KEY_PREFIX}:{session_id}:{section_number}:{field_edited}"
        self.redis.client.set(key, json.dumps(feedback), ex=86400 * 30)
        logger.info("[LearningEngine] Recorded edit: section %s field %s", section_number, field_edited)

    def get_section_history(self, section_number: str, limit: int = 10) -> list:
        """Get past feedback for a section type across all sessions."""
        patterns = []
        cursor = 0
        prefix = f"{PATTERN_KEY_PREFIX}:*:{section_number}"
        while len(patterns) < limit:
            cursor, keys = self.redis.client.scan(cursor, match=prefix, count=50)
            for key in keys:
                raw = self.redis.client.get(key)
                if raw:
                    patterns.append(json.loads(raw))
            if cursor == 0:
                break

        rejections = []
        cursor = 0
        prefix = f"{REJECTION_KEY_PREFIX}:*:{section_number}"
        while len(rejections) < limit:
            cursor, keys = self.redis.client.scan(cursor, match=prefix, count=50)
            for key in keys:
                raw = self.redis.client.get(key)
                if raw:
                    rejections.append(json.loads(raw))
            if cursor == 0:
                break

        return sorted(
            patterns + rejections,
            key=lambda x: x.get("timestamp", ""),
            reverse=True,
        )[:limit]

    def get_failure_patterns(self, section_number: str) -> list:
        """Get only rejections/edits for a section — what NOT to do."""
        history = self.get_section_history(section_number, limit=20)
        return [h for h in history if h.get("event") in ("rejected", "edit")]

    def build_learning_context(self, section_number: str) -> str:
        """Build a context string that can be injected into agent prompts.

        This tells the agent what went wrong in past runs so it avoids
        the same mistakes.
        """
        failures = self.get_failure_patterns(section_number)
        if not failures:
            return ""

        lines = ["LEARNING FROM PAST RUNS (avoid these mistakes):"]
        for f in failures[:5]:
            if f["event"] == "rejected":
                lines.append(f"- Section was REJECTED: {f.get('reason', '')}. CEO said: \"{f.get('ceo_feedback', '')}\"")
            elif f["event"] == "edit":
                lines.append(f"- CEO edited field '{f.get('field', '')}': changed from \"{f.get('original', '')[:100]}\" to \"{f.get('corrected', '')[:100]}\"")

        return "\n".join(lines)

    def record_da_accuracy(
        self,
        session_id: str,
        section_number: str,
        challenge_type: str,
        was_valid: bool,
    ) -> None:
        """Track whether Devil's Advocate challenges turned out to be correct."""
        key = f"learning:da_accuracy:{session_id}:{section_number}:{challenge_type}"
        record = {
            "challenge_type": challenge_type,
            "was_valid": was_valid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.redis.client.set(key, json.dumps(record), ex=86400 * 30)

    def get_da_accuracy_stats(self) -> dict:
        """Get accuracy statistics for Devil's Advocate by challenge type."""
        stats = {}
        cursor = 0
        while True:
            cursor, keys = self.redis.client.scan(cursor, match="learning:da_accuracy:*", count=100)
            for key in keys:
                raw = self.redis.client.get(key)
                if raw:
                    record = json.loads(raw)
                    ct = record.get("challenge_type", "unknown")
                    if ct not in stats:
                        stats[ct] = {"total": 0, "valid": 0}
                    stats[ct]["total"] += 1
                    if record.get("was_valid"):
                        stats[ct]["valid"] += 1
            if cursor == 0:
                break

        for ct in stats:
            total = stats[ct]["total"]
            stats[ct]["accuracy"] = stats[ct]["valid"] / total if total > 0 else 0.0

        return stats
