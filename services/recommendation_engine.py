"""
Recommendation Engine — determines the highest-leverage action for Alex.

Analyzes plan state (coverage, confidence, staleness, contradictions) and
recommends what workspace Alex should use and what action to take.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_highest_leverage_action() -> dict:
    """Determine the single most impactful action Alex can take right now.

    Scoring logic:
    - Stale assumptions with high downstream impact score highest
    - Unresolved contradictions blocking multiple sections score next
    - Empty critical sections (revenue, product) score high
    - General gaps score lowest

    Returns:
        Dict with: action_text, workspace, priority (critical/high/medium/low),
        reasoning.
    """
    try:
        from services.coverage_calculator import (
            get_oldest_assumptions,
            get_contradiction_count,
            get_stale_items,
            get_blocked_sections,
        )

        oldest = get_oldest_assumptions(top_k=3)
        contradiction_count = get_contradiction_count()
        stale = get_stale_items()
        blocked = get_blocked_sections()

        candidates = []

        if oldest and oldest[0]["age_days"] > 30:
            candidates.append({
                "action_text": (
                    f"Validate your oldest assumption ({oldest[0]['age_days']} days old): "
                    f"{oldest[0]['content_preview'][:60]}..."
                ),
                "workspace": "validate",
                "priority": "critical",
                "reasoning": "Unvalidated assumptions compound risk over time. "
                "This one is blocking downstream confidence.",
                "score": oldest[0]["age_days"] * 2,
            })

        if contradiction_count > 0:
            candidates.append({
                "action_text": (
                    f"Resolve {contradiction_count} unresolved contradiction(s) "
                    "before they propagate into the plan."
                ),
                "workspace": "challenge",
                "priority": "high" if contradiction_count >= 3 else "medium",
                "reasoning": "Contradictions in source data produce inconsistent outputs. "
                "Resolve before building.",
                "score": contradiction_count * 20,
            })

        if blocked:
            top_blocked = blocked[0]
            candidates.append({
                "action_text": (
                    f"Feed data for {', '.join(top_blocked['blocked_by'])} — "
                    f"it's blocking {top_blocked['section_id']}."
                ),
                "workspace": "feed",
                "priority": "high",
                "reasoning": "Sections cannot be built without upstream data.",
                "score": len(top_blocked["blocked_by"]) * 25,
            })

        if stale and len(stale) >= 3:
            candidates.append({
                "action_text": (
                    f"Review {len(stale)} stale items (oldest: {stale[0]['age_days']} days). "
                    "Some may no longer be accurate."
                ),
                "workspace": "inspect",
                "priority": "medium",
                "reasoning": "Stale data can silently invalidate dependent sections.",
                "score": len(stale) * 5,
            })

        if not candidates:
            return {
                "action_text": "Feed new data or run a build — the plan needs more information.",
                "workspace": "feed",
                "priority": "low",
                "reasoning": "No critical issues detected. Continue building.",
            }

        candidates.sort(key=lambda c: c["score"], reverse=True)
        winner = candidates[0]
        winner.pop("score", None)
        return winner

    except Exception as e:
        logger.error("[RecommendationEngine] Error computing recommendation: %s", e)
        return {
            "action_text": "Start by feeding data into the system.",
            "workspace": "feed",
            "priority": "low",
            "reasoning": "Unable to compute recommendation — defaulting to FEED.",
        }


def get_section_priority_order() -> list[dict]:
    """Return sections ranked by priority for work.

    Weighted scoring:
    - blocked_downstream_count: how many sections depend on this one (x10)
    - age_of_oldest_item: days since oldest chunk in section (x1)
    - gap_size: number of unfilled nodes in section (x5)

    Returns:
        List of dicts sorted by score descending, each with:
        section_id, title, score, blocked_downstream_count,
        age_of_oldest_item, gap_size.
    """
    try:
        from services.coverage_calculator import get_plan_coverage, get_sections
        from services.dependency_checker import get_downstream_impact

        coverage = get_plan_coverage()
        sections_meta = get_sections()
        per_section = coverage.get("per_section", {})

        results = []
        for section_id, meta in sections_meta.items():
            section_cov = per_section.get(section_id, {})
            total_nodes = section_cov.get("total_nodes", meta.get("node_count", 0))
            filled_nodes = section_cov.get("filled_nodes", 0)
            gap_size = total_nodes - filled_nodes

            downstream = get_downstream_impact(section_id)
            blocked_downstream_count = len(downstream)

            age_of_oldest_item = 0
            try:
                from services.coverage_calculator import get_section_detail

                detail = get_section_detail(section_id)
                ages = [
                    n["age_days"]
                    for n in detail.get("nodes", [])
                    if n.get("age_days") is not None
                ]
                if ages:
                    age_of_oldest_item = max(ages)
            except Exception as age_err:
                logger.warning(
                    "[RecommendationEngine] Cannot get age for %s: %s",
                    section_id,
                    age_err,
                )

            score = (
                blocked_downstream_count * 10
                + age_of_oldest_item * 1
                + gap_size * 5
            )

            results.append({
                "section_id": section_id,
                "title": meta.get("title", ""),
                "score": score,
                "blocked_downstream_count": blocked_downstream_count,
                "age_of_oldest_item": age_of_oldest_item,
                "gap_size": gap_size,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    except Exception as e:
        logger.error(
            "[RecommendationEngine] Error computing section priority: %s", e
        )
        return []


def get_workspace_recommendation() -> str:
    """Return which workspace Alex should use right now.

    Returns:
        Workspace name string (feed/build/inspect/challenge/validate/export).
    """
    action = get_highest_leverage_action()
    return action.get("workspace", "feed")


def suggest_transition(
    current_workspace: str,
    last_action: Optional[str] = None,
) -> Optional[dict]:
    """After an action completes, suggest whether Alex should switch workspaces.

    Args:
        current_workspace: The workspace Alex is currently in.
        last_action: Description of what just happened.

    Returns:
        None if no transition needed, or dict with target_workspace, reason.
    """
    try:
        from services.coverage_calculator import (
            get_contradiction_count,
            get_oldest_assumptions,
        )

        if current_workspace == "feed":
            contradictions = get_contradiction_count()
            if contradictions > 0:
                return {
                    "target_workspace": "challenge",
                    "reason": (
                        f"New data introduced {contradictions} contradiction(s). "
                        "Consider resolving them before building."
                    ),
                }

        if current_workspace == "validate":
            return {
                "target_workspace": "build",
                "reason": "Validation complete — you can now rebuild affected sections with higher confidence.",
            }

        if current_workspace == "challenge":
            oldest = get_oldest_assumptions(top_k=1)
            if oldest and oldest[0]["age_days"] > 14:
                return {
                    "target_workspace": "validate",
                    "reason": (
                        "Challenge surfaced issues. Consider validating your oldest "
                        f"assumption ({oldest[0]['age_days']} days old)."
                    ),
                }

        if current_workspace == "build":
            return {
                "target_workspace": "inspect",
                "reason": "Build complete. Inspect the results to verify quality.",
            }

    except Exception as e:
        logger.error("[RecommendationEngine] Error in suggest_transition: %s", e)

    return None
