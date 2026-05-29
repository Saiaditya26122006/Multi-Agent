"""
Learning Engine — pattern extraction and prompt adaptation for Phase 2.

Goes beyond event logging to:
- Extract structured failure PATTERNS (root cause, trigger field, anti-pattern)
- Build actionable learning context (not just "this failed before")
- Track CEO preferences across runs
- Suggest prompt adjustments after recurring failures
- Measure run-over-run improvement
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

from memory.redis_client import RedisClient

logger = logging.getLogger(__name__)

PATTERN_KEY_PREFIX = "learning:pattern"
FEEDBACK_KEY_PREFIX = "learning:feedback"
REJECTION_KEY_PREFIX = "learning:rejection"
EXTRACTED_PATTERN_PREFIX = "learning:extracted"

ROOT_CAUSE_TYPES = [
    "unsupported_claim",
    "math_error",
    "market_ignorance",
    "generic_filler",
    "contradiction",
    "overconfidence",
    "missing_evidence",
    "wrong_assumption",
    "formatting_error",
]


class LearningEngine:
    """Pattern-extracting learning engine with prompt adaptation."""

    def __init__(self, redis_client: RedisClient, supabase_client=None):
        self.redis = redis_client
        self.supabase = supabase_client

    # ── Event recording ──────────────────────────────────────────────────────

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
        """Record rejection AND extract failure pattern."""
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

        # Extract structured pattern from rejection
        pattern = self._extract_pattern_from_rejection(section_number, reason, ceo_feedback)
        if pattern:
            self._store_extracted_pattern(session_id, section_number, pattern)

    def record_edit(
        self,
        session_id: str,
        section_number: str,
        field_edited: str,
        original_value: str,
        new_value: str,
    ) -> None:
        """Record a CEO edit — implies the agent got it wrong."""
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

        # Extract pattern from edit
        pattern = self._extract_pattern_from_edit(section_number, field_edited, original_value, new_value)
        if pattern:
            self._store_extracted_pattern(session_id, section_number, pattern)

    # ── Pattern extraction ───────────────────────────────────────────────────

    def _extract_pattern_from_rejection(
        self, section_number: str, reason: str, ceo_feedback: str
    ) -> Optional[dict]:
        """Classify rejection into structured pattern without LLM."""
        reason_lower = reason.lower()
        feedback_lower = ceo_feedback.lower()
        combined = reason_lower + " " + feedback_lower

        root_cause = "unsupported_claim"
        if any(w in combined for w in ("math", "number", "calculation", "doesn't add")):
            root_cause = "math_error"
        elif any(w in combined for w in ("generic", "vague", "filler", "meaningless")):
            root_cause = "generic_filler"
        elif any(w in combined for w in ("contradict", "inconsistent", "conflict")):
            root_cause = "contradiction"
        elif any(w in combined for w in ("overconfident", "too high", "not that sure")):
            root_cause = "overconfidence"
        elif any(w in combined for w in ("no evidence", "where did", "source", "proof")):
            root_cause = "missing_evidence"
        elif any(w in combined for w in ("wrong", "incorrect", "not true", "false")):
            root_cause = "wrong_assumption"
        elif any(w in combined for w in ("market", "competitor", "industry")):
            root_cause = "market_ignorance"

        anti_pattern = f"DO NOT: {reason[:200]}"
        positive_pattern = f"INSTEAD: {ceo_feedback[:200]}" if ceo_feedback else ""

        return {
            "root_cause": root_cause,
            "trigger_field": "",
            "anti_pattern": anti_pattern,
            "positive_pattern": positive_pattern,
            "source_event": "rejection",
        }

    def _extract_pattern_from_edit(
        self, section_number: str, field: str, original: str, corrected: str
    ) -> Optional[dict]:
        """Infer pattern from what the CEO changed."""
        original_lower = original.lower()

        root_cause = "unsupported_claim"
        if len(original) < 20 and len(corrected) > 50:
            root_cause = "generic_filler"
        elif any(c.isdigit() for c in original) and any(c.isdigit() for c in corrected):
            root_cause = "math_error"

        return {
            "root_cause": root_cause,
            "trigger_field": field,
            "anti_pattern": f"DO NOT output '{original[:100]}' for field '{field}'",
            "positive_pattern": f"CEO prefers: '{corrected[:100]}'",
            "source_event": "edit",
        }

    def _store_extracted_pattern(
        self, session_id: str, section_number: str, pattern: dict
    ) -> None:
        """Store extracted pattern in Redis."""
        pattern["timestamp"] = datetime.now(timezone.utc).isoformat()
        pattern["session_id"] = session_id
        pattern["section"] = section_number

        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        key = f"{EXTRACTED_PATTERN_PREFIX}:{section_number}:{ts}"
        self.redis.client.set(key, json.dumps(pattern), ex=86400 * 90)
        logger.info(
            "[LearningEngine] Extracted pattern: %s for section %s",
            pattern["root_cause"], section_number,
        )

    # ── Actionable learning context ──────────────────────────────────────────

    def build_learning_context(self, section_number: str) -> str:
        """Build actionable learning context — grouped by root cause."""
        patterns = self._get_extracted_patterns(section_number)
        edits = self._get_ceo_edit_patterns(section_number)

        if not patterns and not edits:
            return ""

        lines = ["LEARNED PATTERNS (from past runs — follow these strictly):"]

        if patterns:
            by_cause = defaultdict(list)
            for p in patterns:
                by_cause[p.get("root_cause", "unknown")].append(p)

            for cause, instances in by_cause.items():
                count = len(instances)
                latest = instances[-1]
                lines.append(f"\n[{cause.upper()}] (occurred {count}x)")
                lines.append(f"  DO NOT: {latest['anti_pattern']}")
                if latest.get("positive_pattern"):
                    lines.append(f"  INSTEAD: {latest['positive_pattern']}")
                if latest.get("trigger_field"):
                    lines.append(f"  WATCH FIELD: {latest['trigger_field']}")

        if edits:
            lines.append("\nCEO PREFERENCES (from manual edits):")
            for edit in edits[:5]:
                lines.append(
                    f"  - Field '{edit.get('field', '?')}': "
                    f"rejected '{edit.get('original', '')[:60]}', "
                    f"preferred '{edit.get('corrected', '')[:60]}'"
                )

        # Recurring error warning
        recurring = self._get_recurring_errors(section_number)
        if recurring:
            lines.append("\nRECURRING ERRORS (fix these or confidence will be capped at 'low'):")
            for cause, count in recurring.items():
                lines.append(f"  - {cause}: failed {count}x")

        return "\n".join(lines)

    def get_prompt_adjustment_suggestion(self, section_number: str) -> Optional[str]:
        """After 3+ failures of same type, suggest SYSTEM_PROMPT change."""
        patterns = self._get_extracted_patterns(section_number)
        if not patterns:
            return None

        cause_counts = Counter(p.get("root_cause", "") for p in patterns)
        recurring = {k: v for k, v in cause_counts.items() if v >= 3}

        if not recurring:
            return None

        top_cause = max(recurring, key=recurring.get)
        relevant = [p for p in patterns if p.get("root_cause") == top_cause]
        latest = relevant[-1]

        return (
            f"Section {section_number} has failed {recurring[top_cause]}x "
            f"due to '{top_cause}'. "
            f"Suggested: {latest.get('positive_pattern', latest.get('anti_pattern', ''))}"
        )

    # ── Data retrieval helpers ───────────────────────────────────────────────

    def _get_extracted_patterns(self, section_number: str) -> list:
        """Get all extracted patterns for a section."""
        patterns = []
        cursor = 0
        prefix = f"{EXTRACTED_PATTERN_PREFIX}:{section_number}:*"
        while len(patterns) < 50:
            cursor, keys = self.redis.client.scan(cursor, match=prefix, count=50)
            for key in keys:
                raw = self.redis.client.get(key)
                if raw:
                    patterns.append(json.loads(raw))
            if cursor == 0:
                break
        return sorted(patterns, key=lambda x: x.get("timestamp", ""))

    def _get_ceo_edit_patterns(self, section_number: str) -> list:
        """Get CEO edit history for a section."""
        edits = []
        cursor = 0
        prefix = f"{FEEDBACK_KEY_PREFIX}:*:{section_number}:*"
        while len(edits) < 20:
            cursor, keys = self.redis.client.scan(cursor, match=prefix, count=50)
            for key in keys:
                raw = self.redis.client.get(key)
                if raw:
                    edits.append(json.loads(raw))
            if cursor == 0:
                break
        return sorted(edits, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]

    def _get_recurring_errors(self, section_number: str) -> dict:
        """Get root causes that have occurred 3+ times."""
        patterns = self._get_extracted_patterns(section_number)
        counts = Counter(p.get("root_cause", "") for p in patterns)
        return {k: v for k, v in counts.items() if v >= 3}

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

    # ── Run-over-run improvement tracking ────────────────────────────────────

    def record_run_score(self, session_id: str, run_id: str, score: float) -> None:
        """Record overall quality score for a pipeline run."""
        record = {
            "session_id": session_id,
            "run_id": run_id,
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        key = f"learning:run_score:{run_id}"
        self.redis.client.set(key, json.dumps(record), ex=86400 * 90)

    def get_improvement_trend(self, limit: int = 10) -> list:
        """Get recent run scores to measure improvement over time."""
        scores = []
        cursor = 0
        while len(scores) < limit:
            cursor, keys = self.redis.client.scan(
                cursor, match="learning:run_score:*", count=50
            )
            for key in keys:
                raw = self.redis.client.get(key)
                if raw:
                    scores.append(json.loads(raw))
            if cursor == 0:
                break
        return sorted(scores, key=lambda x: x.get("timestamp", ""))[-limit:]
