"""
Menu Generator — builds dynamic menus with live stats from the SSoT.

Every menu item shows its current condition: what's blocked, what's ready,
what's urgent. The menu itself is informed by reality, not static text.
"""

import logging
from typing import Optional

from web.workspace_router import Workspace, WORKSPACE_LABELS, WORKSPACE_DESCRIPTIONS

logger = logging.getLogger(__name__)


def generate_dashboard_stats() -> dict:
    """Compute top-bar dashboard statistics.

    Returns:
        Dict with coverage_pct, confidence_pct, contradiction_count, stale_count.
    """
    try:
        from services.coverage_calculator import get_dashboard_stats

        return get_dashboard_stats()
    except Exception as e:
        logger.error("[MenuGen] Error generating dashboard stats: %s", e)
        return {
            "coverage_pct": 0.0,
            "confidence_pct": 0.0,
            "contradiction_count": 0,
            "stale_count": 0,
            "oldest_assumption_age_days": 0,
        }


def generate_recommendation() -> dict:
    """Get the current system recommendation.

    Returns:
        Dict with action_text, workspace, priority, reasoning.
    """
    try:
        from services.recommendation_engine import get_highest_leverage_action

        return get_highest_leverage_action()
    except Exception as e:
        logger.error("[MenuGen] Error generating recommendation: %s", e)
        return {
            "action_text": "Start by feeding data into the system.",
            "workspace": "feed",
            "priority": "low",
            "reasoning": "",
        }


def generate_main_menu(session_id: Optional[str] = None) -> dict:
    """Build the main menu with live badges and status per workspace.

    Each menu item includes:
    - id: workspace enum value
    - label: display name
    - description: one-line description
    - badge: live stat relevant to this workspace (or None)
    - status: "ready" | "urgent" | "blocked" | "warning"

    Args:
        session_id: Optional session for personalized menu state.

    Returns:
        Dict with items (list), dashboard (stats), recommendation (dict).
    """
    dashboard = generate_dashboard_stats()
    recommendation = generate_recommendation()

    stale_count = dashboard.get("stale_count", 0)
    contradiction_count = dashboard.get("contradiction_count", 0)
    oldest_age = dashboard.get("oldest_assumption_age_days", 0)
    coverage_pct = dashboard.get("coverage_pct", 0.0)

    try:
        from services.coverage_calculator import get_blocked_sections

        blocked_sections = get_blocked_sections()
        blocked_count = len(blocked_sections)
    except Exception:
        blocked_count = 0

    items = [
        {
            "id": Workspace.FEED.value,
            "number": "1",
            "label": WORKSPACE_LABELS[Workspace.FEED],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.FEED],
            "badge": f"{stale_count} stale items" if stale_count > 0 else None,
            "status": "ready",
        },
        {
            "id": Workspace.BUILD.value,
            "number": "2",
            "label": WORKSPACE_LABELS[Workspace.BUILD],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.BUILD],
            "badge": f"{blocked_count} sections blocked" if blocked_count > 0 else None,
            "status": "blocked" if blocked_count > 3 else ("warning" if blocked_count > 0 else "ready"),
        },
        {
            "id": Workspace.INSPECT.value,
            "number": "3",
            "label": WORKSPACE_LABELS[Workspace.INSPECT],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.INSPECT],
            "badge": None,
            "status": "ready",
        },
        {
            "id": Workspace.CHALLENGE.value,
            "number": "4",
            "label": WORKSPACE_LABELS[Workspace.CHALLENGE],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.CHALLENGE],
            "badge": f"{contradiction_count} contradictions" if contradiction_count > 0 else None,
            "status": "urgent" if contradiction_count >= 5 else "ready",
        },
        {
            "id": Workspace.VALIDATE.value,
            "number": "5",
            "label": WORKSPACE_LABELS[Workspace.VALIDATE],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.VALIDATE],
            "badge": f"{oldest_age}d oldest" if oldest_age > 14 else None,
            "status": "urgent" if oldest_age > 30 else "ready",
        },
        {
            "id": Workspace.EXPORT.value,
            "number": "6",
            "label": WORKSPACE_LABELS[Workspace.EXPORT],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.EXPORT],
            "badge": f"{coverage_pct:.0f}% complete",
            "status": "warning" if coverage_pct < 60 else "ready",
        },
        {
            "id": Workspace.AUTO.value,
            "number": "7",
            "label": WORKSPACE_LABELS[Workspace.AUTO],
            "description": WORKSPACE_DESCRIPTIONS[Workspace.AUTO],
            "badge": None,
            "status": "ready",
        },
    ]

    return {
        "items": items,
        "dashboard": dashboard,
        "recommendation": recommendation,
    }


def generate_sub_menu(workspace: Workspace) -> dict:
    """Build the sub-menu for a specific workspace with live state.

    Args:
        workspace: The workspace to generate sub-menu for.

    Returns:
        Dict with workspace id, options (list), and context_stats (dict).
    """
    if workspace == Workspace.FEED:
        return _feed_sub_menu()
    elif workspace == Workspace.BUILD:
        return _build_sub_menu()
    elif workspace == Workspace.INSPECT:
        return _inspect_sub_menu()
    elif workspace == Workspace.CHALLENGE:
        return _challenge_sub_menu()
    elif workspace == Workspace.VALIDATE:
        return _validate_sub_menu()
    elif workspace == Workspace.EXPORT:
        return _export_sub_menu()
    else:
        return {"workspace": workspace.value, "options": [], "context_stats": {}}


def _feed_sub_menu() -> dict:
    stats = {}
    try:
        from services.coverage_calculator import get_stale_items

        stale = get_stale_items()
        stats["stale_count"] = len(stale)
    except Exception:
        stats["stale_count"] = 0

    options = [
        {"key": "A", "label": "Paste raw text / brain dump", "action": "raw_text"},
        {"key": "B", "label": "Correct something I told you before", "action": "correction"},
        {"key": "C", "label": "Add a specific fact to a specific section", "action": "targeted_add"},
        {"key": "D", "label": "Upload structured data (CSV/table)", "action": "structured_upload"},
        {"key": "E", "label": "Just type freely — I'll figure it out", "action": "freeform"},
    ]

    return {
        "workspace": Workspace.FEED.value,
        "options": options,
        "context_stats": stats,
        "hint": "Sections starving for data: Revenue (sec 9), Team (sec 14)",
    }


def _build_sub_menu() -> dict:
    stats = {}
    try:
        from services.coverage_calculator import get_blocked_sections

        blocked = get_blocked_sections()
        stats["blocked_sections"] = blocked
        stats["blocked_count"] = len(blocked)
    except Exception:
        stats["blocked_sections"] = []
        stats["blocked_count"] = 0

    options = [
        {"key": "A", "label": "Full plan (all sections)", "action": "full_plan"},
        {"key": "B", "label": "Single section — pick which one", "action": "single_section"},
        {"key": "C", "label": "Re-run with new data (incremental)", "action": "incremental"},
        {"key": "D", "label": "Only the weak sections (confidence < 40%)", "action": "weak_only"},
    ]

    return {
        "workspace": Workspace.BUILD.value,
        "options": options,
        "context_stats": stats,
    }


def _inspect_sub_menu() -> dict:
    options = [
        {"key": "A", "label": "Coverage heatmap — which sections are full/empty", "action": "coverage"},
        {"key": "B", "label": "Confidence breakdown — CONFIRMED vs ASSUMPTION vs gaps", "action": "confidence"},
        {"key": "C", "label": "Contradictions — what conflicts with what", "action": "contradictions"},
        {"key": "D", "label": "Stale data — what's old and needs refreshing", "action": "stale"},
        {"key": "E", "label": "Dependency chain — what's blocking what", "action": "dependencies"},
        {"key": "F", "label": "Specific section deep-dive — pick which one", "action": "section_dive"},
    ]

    return {
        "workspace": Workspace.INSPECT.value,
        "options": options,
        "context_stats": {},
    }


def _challenge_sub_menu() -> dict:
    stats = {}
    try:
        from services.coverage_calculator import get_oldest_assumptions

        oldest = get_oldest_assumptions(top_k=3)
        stats["vulnerable_assumptions"] = oldest
    except Exception:
        stats["vulnerable_assumptions"] = []

    options = [
        {"key": "A", "label": "My weakest assumptions (system picks)", "action": "weakest"},
        {"key": "B", "label": "A specific section", "action": "section"},
        {"key": "C", "label": "A specific claim I made", "action": "claim"},
        {"key": "D", "label": "Full devil's advocate on the entire plan", "action": "full_da"},
        {"key": "E", "label": "Competitor comparison — how do I stack up?", "action": "competitor"},
    ]

    return {
        "workspace": Workspace.CHALLENGE.value,
        "options": options,
        "context_stats": stats,
    }


def _validate_sub_menu() -> dict:
    stats = {}
    try:
        from services.coverage_calculator import get_oldest_assumptions

        oldest = get_oldest_assumptions(top_k=5)
        stats["assumption_queue"] = oldest
    except Exception:
        stats["assumption_queue"] = []

    options = [
        {"key": "A", "label": "Confirm an assumption (I have evidence now)", "action": "confirm"},
        {"key": "B", "label": "Kill an assumption (it was wrong)", "action": "kill"},
        {"key": "C", "label": "Report a customer conversation", "action": "conversation"},
        {"key": "D", "label": "Update a decision I made earlier", "action": "update_decision"},
    ]

    return {
        "workspace": Workspace.VALIDATE.value,
        "options": options,
        "context_stats": stats,
    }


def _export_sub_menu() -> dict:
    stats = {}
    try:
        from services.coverage_calculator import get_dashboard_stats

        dash = get_dashboard_stats()
        stats["coverage_pct"] = dash.get("coverage_pct", 0.0)
    except Exception:
        stats["coverage_pct"] = 0.0

    options = [
        {"key": "A", "label": "Full business plan (DOCX)", "action": "full_docx"},
        {"key": "B", "label": "Executive summary only (1-pager)", "action": "exec_summary"},
        {"key": "C", "label": "Investor pitch version (hides uncertainties)", "action": "investor"},
        {"key": "D", "label": "Internal version (shows all epistemic tags)", "action": "internal"},
        {"key": "E", "label": "Gap report (what's missing before submission)", "action": "gap_report"},
    ]

    return {
        "workspace": Workspace.EXPORT.value,
        "options": options,
        "context_stats": stats,
    }


def format_menu_as_text(menu: dict) -> str:
    """Format the main menu as a chat-friendly text message.

    Args:
        menu: The menu dict from generate_main_menu().

    Returns:
        Formatted string for display in the chat panel.
    """
    dashboard = menu.get("dashboard", {})
    recommendation = menu.get("recommendation", {})
    items = menu.get("items", [])

    lines = []
    lines.append("=" * 50)
    lines.append("EpistemicOS — Business Plan Builder")
    lines.append("")
    lines.append(
        f"  Coverage: {dashboard.get('coverage_pct', 0):.0f}%  |  "
        f"Confidence: {dashboard.get('confidence_pct', 0):.0f}%  |  "
        f"Contradictions: {dashboard.get('contradiction_count', 0)}  |  "
        f"Stale: {dashboard.get('stale_count', 0)}"
    )
    lines.append("")

    if recommendation.get("action_text"):
        priority = recommendation.get("priority", "").upper()
        lines.append(f"  [{priority}] {recommendation['action_text']}")
        lines.append("")

    lines.append("-" * 50)
    lines.append("")

    for item in items:
        badge_str = f"  ({item['badge']})" if item.get("badge") else ""
        status_icon = ""
        if item["status"] == "urgent":
            status_icon = " !"
        elif item["status"] == "blocked":
            status_icon = " [BLOCKED]"
        elif item["status"] == "warning":
            status_icon = " [!]"

        lines.append(
            f"  [{item['number']}] {item['label']}{status_icon}{badge_str}"
        )
        lines.append(f"      {item['description']}")
        lines.append("")

    lines.append("Type a number to enter a workspace, or just start talking.")
    lines.append("=" * 50)

    return "\n".join(lines)


def is_first_time_user() -> bool:
    """Check if the knowledge base has any data (new user with empty state)."""
    try:
        from services.coverage_calculator import get_dashboard_stats
        stats = get_dashboard_stats()
        return stats.get("coverage_pct", 0) == 0
    except Exception:
        return True


def format_sub_menu_as_text(sub_menu: dict) -> str:
    """Format a workspace sub-menu as a chat-friendly text message.

    Args:
        sub_menu: The sub-menu dict from generate_sub_menu().

    Returns:
        Formatted string for display in the chat panel.
    """
    workspace = sub_menu.get("workspace", "")
    options = sub_menu.get("options", [])
    hint = sub_menu.get("hint", "")

    lines = []
    lines.append(f"  {WORKSPACE_LABELS.get(Workspace(workspace), workspace).upper()}")
    lines.append("-" * 40)
    lines.append("")

    for opt in options:
        lines.append(f"  [{opt['key']}] {opt['label']}")

    if hint:
        lines.append("")
        lines.append(f"  Hint: {hint}")

    lines.append("")
    lines.append("Type a letter, or describe what you want in plain text.")
    lines.append("Type 'back' to return to the main menu.")

    return "\n".join(lines)
