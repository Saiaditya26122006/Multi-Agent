"""
Epistemic Tagger — infers certainty level from language cues.

Categorizes facts as CONFIRMED, ASSUMPTION, INFERRED, or CONTRADICTION
based on linguistic markers. Falls back to INFERRED when no clear signal.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

CONFIRMED_PATTERNS = [
    (r"\b(confirmed|verified|proven|validated)\b", 0.9),
    (r"\b(they said|he said|she said|they confirmed)\b", 0.85),
    (r"\b(the contract states|the data shows|evidence shows)\b", 0.9),
    (r"\b(we know|it is certain|it's confirmed)\b", 0.85),
    (r"\b(documented|measured|observed|recorded)\b", 0.8),
    (r"\b(signed|agreed|committed)\b", 0.85),
]

ASSUMPTION_PATTERNS = [
    (r"\b(i think|we think|we believe)\b", 0.9),
    (r"\b(maybe|perhaps|possibly)\b", 0.85),
    (r"\b(hypothesis|hypothesize|hypothetical)\b", 0.9),
    (r"\b(likely|probably|presumably)\b", 0.8),
    (r"\b(we assume|assumption|assumed)\b", 0.95),
    (r"\b(should be|expected to|anticipated)\b", 0.75),
    (r"\b(might|could be|may be)\b", 0.8),
    (r"\b(initial estimate|rough estimate|best guess)\b", 0.85),
    (r"\b(unvalidated|untested|unproven)\b", 0.9),
]

CONTRADICTION_PATTERNS = [
    (r"\b(contradicts|conflicts with|inconsistent with)\b", 0.95),
    (r"\b(but earlier|previously said|changed from)\b", 0.8),
    (r"\b(on the other hand|however.*previously)\b", 0.75),
    (r"\b(no longer|was wrong|incorrect)\b", 0.8),
    (r"\b(disagrees with|clashes with)\b", 0.9),
]


def tag_from_language(fact: str) -> dict:
    """Infer epistemic status from language cues in a fact.

    Uses regex pattern matching against known certainty markers.

    Args:
        fact: The fact text to tag.

    Returns:
        Dict with: epistemic_status, confidence, cues_found.
    """
    text_lower = fact.lower()
    cues_found = []

    best_contradiction = 0.0
    for pattern, weight in CONTRADICTION_PATTERNS:
        if re.search(pattern, text_lower):
            best_contradiction = max(best_contradiction, weight)
            cues_found.append(("contradiction", pattern))

    best_confirmed = 0.0
    for pattern, weight in CONFIRMED_PATTERNS:
        if re.search(pattern, text_lower):
            best_confirmed = max(best_confirmed, weight)
            cues_found.append(("confirmed", pattern))

    best_assumption = 0.0
    for pattern, weight in ASSUMPTION_PATTERNS:
        if re.search(pattern, text_lower):
            best_assumption = max(best_assumption, weight)
            cues_found.append(("assumption", pattern))

    if best_contradiction > best_confirmed and best_contradiction > best_assumption:
        return {
            "epistemic_status": "CONTRADICTION",
            "confidence": best_contradiction,
            "cues_found": cues_found,
        }

    if best_confirmed > best_assumption:
        return {
            "epistemic_status": "CONFIRMED",
            "confidence": best_confirmed,
            "cues_found": cues_found,
        }

    if best_assumption > 0:
        return {
            "epistemic_status": "ASSUMPTION",
            "confidence": best_assumption,
            "cues_found": cues_found,
        }

    return {
        "epistemic_status": "INFERRED",
        "confidence": 0.5,
        "cues_found": [],
    }


def tag_batch(facts: list[str]) -> list[dict]:
    """Tag multiple facts efficiently.

    Args:
        facts: List of fact text strings.

    Returns:
        List of dicts, each with: fact, epistemic_status, confidence, cues_found.
    """
    results = []
    for fact in facts:
        tag = tag_from_language(fact)
        results.append({
            "fact": fact,
            "epistemic_status": tag["epistemic_status"],
            "confidence": tag["confidence"],
            "cues_found": tag["cues_found"],
        })
    return results


def enforce_prefix(signal: str, status: str) -> str:
    """Prepend epistemic status tag to extracted signal.

    Ensures every signal text starts with [STATUS] prefix so the
    certainty level is never lost in downstream processing.

    Args:
        signal: The extracted signal text.
        status: The epistemic status (CONFIRMED, ASSUMPTION, etc.)

    Returns:
        Signal with prefix, e.g. "[ASSUMPTION] Annual SaaS pricing model"
    """
    prefix = f"[{status}]"
    if signal.startswith("["):
        existing = re.match(r"^\[([A-Z_]+)\]", signal)
        if existing:
            return signal
    return f"{prefix} {signal}"
