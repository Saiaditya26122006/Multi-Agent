"""
Preference Extractor — derives implicit CEO preferences from decision patterns.

Analyzes repeated decisions/feedback to detect patterns like:
- "Alex consistently rejects optimistic projections"
- "Alex prefers conservative estimates"
- "Alex always picks institutional over B2C framing"

Stores derived patterns as preference_pattern chunks in the RAG.
"""

import logging
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

MIN_PATTERN_COUNT = 3
PATTERN_CONFIDENCE_BASE = 0.6


def extract_patterns(
    session_id: Optional[str] = None,
    min_decisions: int = MIN_PATTERN_COUNT,
) -> list[dict]:
    """Analyze recent decisions/feedback to detect preference patterns.

    Args:
        session_id: Limit analysis to specific session (None = all).
        min_decisions: Minimum occurrences before declaring a pattern.

    Returns:
        List of detected patterns (may be empty if insufficient data).
    """
    from services.rag_service import retrieve

    decisions = retrieve(
        query="CEO decision feedback rejection adjustment",
        source_types=["decision", "feedback", "negative_knowledge"],
        top_k=50,
        threshold=0.3,
        recency_boost=True,
    )

    if len(decisions) < min_decisions:
        logger.debug(
            "[Preferences] Only %d decisions found, need %d",
            len(decisions),
            min_decisions,
        )
        return []

    patterns = []

    kill_reasons = []
    adjust_reasons = []
    for chunk in decisions:
        meta = chunk.metadata or {}
        if meta.get("decision") == "kill":
            kill_reasons.append(chunk.content)
        elif meta.get("decision") == "adjust":
            adjust_reasons.append(chunk.content)
        elif chunk.source_type == "feedback":
            adjust_reasons.append(chunk.content)

    if len(kill_reasons) >= min_decisions:
        pattern = _detect_theme(kill_reasons, "rejection")
        if pattern:
            patterns.append(pattern)

    if len(adjust_reasons) >= min_decisions:
        pattern = _detect_theme(adjust_reasons, "adjustment")
        if pattern:
            patterns.append(pattern)

    return patterns


def _detect_theme(texts: list[str], pattern_type: str) -> Optional[dict]:
    """Detect common themes across a set of decision texts.

    Uses keyword frequency to find repeated themes.
    """
    keywords = Counter()
    theme_words = {
        "optimistic", "conservative", "realistic", "generic",
        "specific", "detailed", "vague", "broad", "narrow",
        "institutional", "individual", "b2c", "b2b",
        "pricing", "financial", "market", "competitor",
        "evidence", "assumption", "validated", "unvalidated",
    }

    for text in texts:
        text_lower = text.lower()
        for word in theme_words:
            if word in text_lower:
                keywords[word] += 1

    if not keywords:
        return None

    top_themes = keywords.most_common(3)
    dominant_theme = top_themes[0]

    if dominant_theme[1] < 2:
        return None

    confidence = min(
        1.0,
        PATTERN_CONFIDENCE_BASE + (dominant_theme[1] / len(texts)) * 0.4,
    )

    pattern_content = (
        f"CEO preference pattern ({pattern_type}): "
        f"Frequently mentions '{dominant_theme[0]}' in {pattern_type}s "
        f"({dominant_theme[1]}/{len(texts)} occurrences). "
        f"Related themes: {', '.join(t[0] for t in top_themes[1:] if t[1] >= 2)}"
    )

    return {
        "content": pattern_content,
        "confidence": confidence,
        "theme": dominant_theme[0],
        "count": dominant_theme[1],
        "total_decisions": len(texts),
        "pattern_type": pattern_type,
    }


def store_patterns(patterns: list[dict]) -> list[str]:
    """Store detected preference patterns into the RAG.

    Args:
        patterns: List of pattern dicts from extract_patterns().

    Returns:
        List of stored chunk IDs.
    """
    from services.rag_service import store

    ids = []
    for pattern in patterns:
        chunk_id = store(
            content=pattern["content"],
            source_type="preference_pattern",
            confidence=pattern["confidence"],
            topic_tags=["preference", pattern["pattern_type"], pattern["theme"]],
            metadata={
                "theme": pattern["theme"],
                "count": pattern["count"],
                "total_decisions": pattern["total_decisions"],
                "pattern_type": pattern["pattern_type"],
            },
        )
        if chunk_id:
            ids.append(chunk_id)
            logger.info(
                "[Preferences] Stored pattern: %s (confidence=%.2f)",
                pattern["theme"],
                pattern["confidence"],
            )

    return ids


def run_extraction() -> list[str]:
    """Convenience: extract and store patterns in one call.

    Returns:
        List of stored chunk IDs.
    """
    patterns = extract_patterns()
    if patterns:
        return store_patterns(patterns)
    return []
