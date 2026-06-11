"""
Learning Engine — pattern extraction and prompt adaptation for Phase 2.

Goes beyond event logging to:
- Extract structured failure PATTERNS (root cause, trigger field, anti-pattern)
- Build actionable learning context (not just "this failed before")
- Track CEO preferences across runs
- Suggest prompt adjustments after recurring failures
- Measure run-over-run improvement

P2-2: Sliding-Window Learning Context with relevance decay.
Maintains longer-term memory with time-based and frequency-based relevance scoring.
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from memory.redis_client import RedisClient

logger = logging.getLogger(__name__)

PATTERN_KEY_PREFIX = "learning:pattern"
FEEDBACK_KEY_PREFIX = "learning:feedback"
REJECTION_KEY_PREFIX = "learning:rejection"
EXTRACTED_PATTERN_PREFIX = "learning:extracted"

# P2-2: Sliding window parameters
RELEVANCE_WINDOW_DAYS = 90  # Keep patterns for 90 days
DECAY_HALF_LIFE_DAYS = 30  # Relevance halves every 30 days
MIN_RELEVANCE_SCORE = 0.1  # Drop patterns below this threshold

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
        """P2-2: Build actionable learning context with relevance decay.

        Patterns are scored by recency + frequency. Recent patterns get higher weight.
        Older patterns decay exponentially but stay available if frequently recurring.
        """
        patterns = self._get_extracted_patterns(section_number)
        edits = self._get_ceo_edit_patterns(section_number)

        if not patterns and not edits:
            return ""

        # P2-2: Score patterns by relevance (recency + frequency)
        scored_patterns = self._score_patterns_by_relevance(patterns)

        # Filter out low-relevance patterns
        relevant_patterns = [
            p for p in scored_patterns
            if p["relevance_score"] >= MIN_RELEVANCE_SCORE
        ]

        if not relevant_patterns and not edits:
            return ""

        lines = [
            "LEARNED PATTERNS (from past runs — follow these strictly):",
            f"(Relevance window: {RELEVANCE_WINDOW_DAYS} days, showing top patterns by recency + frequency)"
        ]

        if relevant_patterns:
            by_cause = defaultdict(list)
            for p in relevant_patterns:
                by_cause[p.get("root_cause", "unknown")].append(p)

            for cause, instances in sorted(
                by_cause.items(),
                key=lambda x: sum(p["relevance_score"] for p in x[1]),
                reverse=True
            ):
                count = len(instances)
                # Use highest-scored instance as representative
                best = max(instances, key=lambda p: p["relevance_score"])
                relevance_pct = int(best["relevance_score"] * 100)

                lines.append(
                    f"\n[{cause.upper()}] (occurred {count}x, relevance: {relevance_pct}%)"
                )
                lines.append(f"  DO NOT: {best['anti_pattern']}")
                if best.get("positive_pattern"):
                    lines.append(f"  INSTEAD: {best['positive_pattern']}")
                if best.get("trigger_field"):
                    lines.append(f"  WATCH FIELD: {best['trigger_field']}")

                # P2-2: Show decay status for older patterns
                age_days = best.get("age_days", 0)
                if age_days > 30:
                    lines.append(f"  NOTE: Pattern is {age_days} days old — verify still applicable")

        if edits:
            # P2-2: Score edits by relevance too
            scored_edits = self._score_edits_by_relevance(edits)
            relevant_edits = [e for e in scored_edits if e["relevance_score"] >= MIN_RELEVANCE_SCORE]

            if relevant_edits:
                lines.append("\nCEO PREFERENCES (from manual edits):")
                for edit in relevant_edits[:5]:
                    relevance_pct = int(edit["relevance_score"] * 100)
                    lines.append(
                        f"  - Field '{edit.get('field', '?')}' (rel: {relevance_pct}%): "
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

    # ── P2-2: Relevance scoring with time decay ──────────────────────────────

    def _score_patterns_by_relevance(self, patterns: list) -> list:
        """P2-2: Score patterns by recency + frequency with exponential decay.

        Relevance = (recency_score × 0.7) + (frequency_score × 0.3)

        Recency score: exponential decay — halves every DECAY_HALF_LIFE_DAYS
        Frequency score: normalized occurrence count (more occurrences = higher score)
        """
        if not patterns:
            return []

        now = datetime.now(timezone.utc)

        # Count occurrences by root cause
        cause_counts = Counter(p.get("root_cause", "") for p in patterns)
        max_count = max(cause_counts.values()) if cause_counts else 1

        scored = []
        for pattern in patterns:
            # Parse timestamp
            ts_str = pattern.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = now - timedelta(days=90)  # Default to oldest if parse fails

            age_days = (now - ts).days

            # Recency score: exponential decay (1.0 at day 0, 0.5 at DECAY_HALF_LIFE_DAYS)
            recency_score = 2 ** (-age_days / DECAY_HALF_LIFE_DAYS)

            # Frequency score: normalized by max occurrence
            root_cause = pattern.get("root_cause", "")
            frequency_score = cause_counts.get(root_cause, 1) / max_count

            # Combined relevance (weighted average)
            relevance = (recency_score * 0.7) + (frequency_score * 0.3)

            scored.append({
                **pattern,
                "relevance_score": relevance,
                "recency_score": recency_score,
                "frequency_score": frequency_score,
                "age_days": age_days,
            })

        # Sort by relevance (highest first)
        return sorted(scored, key=lambda p: p["relevance_score"], reverse=True)

    def _score_edits_by_relevance(self, edits: list) -> list:
        """P2-2: Score CEO edits by recency (no frequency component for edits)."""
        if not edits:
            return []

        now = datetime.now(timezone.utc)

        scored = []
        for edit in edits:
            ts_str = edit.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = now - timedelta(days=90)

            age_days = (now - ts).days

            # Edits use recency-only score (each edit is unique, no frequency)
            recency_score = 2 ** (-age_days / DECAY_HALF_LIFE_DAYS)

            scored.append({
                **edit,
                "relevance_score": recency_score,
                "age_days": age_days,
            })

        return sorted(scored, key=lambda e: e["relevance_score"], reverse=True)

    def get_sliding_window_stats(self, section_number: str) -> dict:
        """P2-2: Get statistics about the learning window for a section.

        Returns:
            - total_patterns: count of all patterns in Redis
            - relevant_patterns: count above MIN_RELEVANCE_SCORE threshold
            - avg_age_days: average age of relevant patterns
            - oldest_relevant_days: age of oldest still-relevant pattern
            - decay_rate: current decay rate (patterns dropping per day)
        """
        patterns = self._get_extracted_patterns(section_number)
        if not patterns:
            return {
                "total_patterns": 0,
                "relevant_patterns": 0,
                "avg_age_days": 0,
                "oldest_relevant_days": 0,
                "decay_rate": 0.0,
            }

        scored = self._score_patterns_by_relevance(patterns)
        relevant = [p for p in scored if p["relevance_score"] >= MIN_RELEVANCE_SCORE]

        if not relevant:
            return {
                "total_patterns": len(patterns),
                "relevant_patterns": 0,
                "avg_age_days": 0,
                "oldest_relevant_days": 0,
                "decay_rate": 0.0,
            }

        avg_age = sum(p["age_days"] for p in relevant) / len(relevant)
        oldest_age = max(p["age_days"] for p in relevant)

        # Estimate decay rate: patterns that dropped below threshold in last 7 days
        now = datetime.now(timezone.utc)
        recent_dropped = [
            p for p in scored
            if p["relevance_score"] < MIN_RELEVANCE_SCORE
            and p["age_days"] <= 37  # Within one decay period + buffer
        ]
        decay_rate = len(recent_dropped) / 7 if recent_dropped else 0.0

        return {
            "total_patterns": len(patterns),
            "relevant_patterns": len(relevant),
            "avg_age_days": round(avg_age, 1),
            "oldest_relevant_days": oldest_age,
            "decay_rate": round(decay_rate, 2),
        }

    def prune_expired_patterns(self, section_number: Optional[str] = None) -> int:
        """P2-2: Remove patterns older than RELEVANCE_WINDOW_DAYS.

        Called periodically to clean up Redis. Returns count of deleted keys.

        If section_number provided, prune only that section. Otherwise prune all.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=RELEVANCE_WINDOW_DAYS)
        deleted = 0

        pattern_prefix = (
            f"{EXTRACTED_PATTERN_PREFIX}:{section_number}:*"
            if section_number
            else f"{EXTRACTED_PATTERN_PREFIX}:*"
        )

        cursor = 0
        while True:
            cursor, keys = self.redis.client.scan(cursor, match=pattern_prefix, count=100)
            for key in keys:
                raw = self.redis.client.get(key)
                if not raw:
                    continue

                try:
                    pattern = json.loads(raw)
                    ts_str = pattern.get("timestamp", "")
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

                    if ts < cutoff:
                        self.redis.client.delete(key)
                        deleted += 1
                except (ValueError, json.JSONDecodeError, AttributeError):
                    # Malformed pattern — delete it
                    self.redis.client.delete(key)
                    deleted += 1

            if cursor == 0:
                break

        if deleted > 0:
            logger.info(
                "[LearningEngine] Pruned %d expired patterns (older than %d days)",
                deleted, RELEVANCE_WINDOW_DAYS
            )

        return deleted
