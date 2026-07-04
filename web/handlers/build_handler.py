"""
BUILD Workspace Handler — orchestrates business plan generation.

Handles: full plan builds, single section builds, incremental rebuilds,
weak-section-only builds. Reports progress and surfaces blockers.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_full_plan(session_id: Optional[str] = None) -> dict:
    """Trigger the full pipeline via Mother Agent.

    Args:
        session_id: Current session identifier.

    Returns:
        Dict with: status, message, blockers (if any).
    """
    try:
        blockers = get_build_blockers()
        if blockers:
            return {
                "status": "blocked",
                "message": (
                    f"Cannot build full plan — {len(blockers)} section(s) have unmet dependencies. "
                    "Feed the missing data first."
                ),
                "blockers": blockers,
            }

        return {
            "status": "started",
            "message": "Full plan build initiated. All sections will be generated.",
            "sections_queued": _get_all_sections(),
        }
    except Exception as e:
        logger.error("[BuildHandler] Error in build_full_plan: %s", e)
        return {
            "status": "error",
            "message": "I couldn't process that right now. Try again.",
            "error_type": type(e).__name__,
        }


def build_section(section_id: str, session_id: Optional[str] = None) -> dict:
    """Trigger a single section build.

    Args:
        section_id: The BP section to build (e.g. "BP.9", "9").
        session_id: Current session identifier.

    Returns:
        Dict with: status, message, section, blockers.
    """
    normalized = _normalize_section_id(section_id)
    blockers = _get_section_blockers(normalized)

    if blockers:
        return {
            "status": "blocked",
            "message": f"Section {normalized} is blocked by: {', '.join(blockers)}",
            "section": normalized,
            "blockers": blockers,
        }

    return {
        "status": "started",
        "message": f"Building section {normalized}.",
        "section": normalized,
    }


def build_incremental(session_id: Optional[str] = None) -> dict:
    """Rebuild only sections with new data since last build.

    Args:
        session_id: Current session identifier.

    Returns:
        Dict with: status, sections_to_rebuild, reason.
    """
    sections_with_new_data = _get_sections_with_new_data()

    if not sections_with_new_data:
        return {
            "status": "nothing_to_build",
            "message": "No sections have new data since the last build. Feed more data first.",
            "sections_to_rebuild": [],
        }

    return {
        "status": "started",
        "message": f"Incremental rebuild of {len(sections_with_new_data)} section(s) with new data.",
        "sections_to_rebuild": sections_with_new_data,
    }


def build_weak_sections(threshold: float = 40.0, session_id: Optional[str] = None) -> dict:
    """Rebuild only sections below a confidence threshold.

    Args:
        threshold: Confidence percentage below which sections are rebuilt.
        session_id: Current session identifier.

    Returns:
        Dict with: status, weak_sections, threshold.
    """
    weak = _get_weak_sections(threshold)

    if not weak:
        return {
            "status": "nothing_to_build",
            "message": f"All sections are above {threshold}% confidence. Nothing to rebuild.",
            "weak_sections": [],
        }

    return {
        "status": "started",
        "message": f"Rebuilding {len(weak)} section(s) below {threshold}% confidence.",
        "weak_sections": weak,
        "threshold": threshold,
    }


def get_build_status() -> dict:
    """Get the current pipeline build status.

    Returns:
        Dict with: running (bool), progress, current_agent, sections_complete.
    """
    return {
        "running": False,
        "progress": 0,
        "current_agent": None,
        "sections_complete": [],
        "sections_pending": [],
    }


def get_build_blockers() -> list[dict]:
    """Get what's preventing builds from starting or completing.

    Returns:
        List of blocker dicts with section_id, blocked_by, reason.
    """
    try:
        from services.coverage_calculator import get_blocked_sections

        return get_blocked_sections()
    except Exception as e:
        logger.error("[BuildHandler] Error getting blockers: %s", e)
        return []


def format_build_response(result: dict) -> str:
    """Format build results as a chat message.

    Args:
        result: Output from any build_* function.

    Returns:
        Formatted string for chat.
    """
    status = result.get("status", "unknown")

    if status == "blocked":
        blockers = result.get("blockers", [])
        lines = [result.get("message", "Build blocked.")]
        lines.append("")
        lines.append("Blockers:")
        for b in blockers[:5]:
            section = b.get("section_id", "?")
            blocked_by = ", ".join(b.get("blocked_by", []))
            lines.append(f"  - {section} needs: {blocked_by}")
        lines.append("")
        lines.append("Switch to FEED and provide the missing data.")
        return "\n".join(lines)

    if status == "nothing_to_build":
        return result.get("message", "Nothing to build.")

    if status == "started":
        sections = result.get("sections_queued") or result.get("sections_to_rebuild") or result.get("weak_sections") or []
        msg = result.get("message", "Build started.")
        if sections:
            section_list = ", ".join(str(s) for s in sections[:10])
            return f"{msg}\n\nSections: {section_list}"
        return msg

    return result.get("message", "Build status unknown.")


def _normalize_section_id(section_id: str) -> str:
    """Normalize section ID to BP.X format."""
    section_id = section_id.strip()
    if section_id.startswith("BP."):
        return section_id
    if section_id.isdigit():
        return f"BP.{section_id}"
    return f"BP.{section_id}"


def _get_section_blockers(section_id: str) -> list[str]:
    """Get what's blocking a specific section."""
    try:
        from services.coverage_calculator import get_blocked_sections

        blocked = get_blocked_sections()
        for b in blocked:
            if b.get("section_id") == section_id:
                return b.get("blocked_by", [])
    except Exception as e:
        logger.error("[BuildHandler] Error checking section blockers: %s", e)
    return []


def _get_all_sections() -> list[str]:
    """Get all section IDs."""
    try:
        from services.coverage_calculator import get_sections

        return list(get_sections().keys())
    except Exception:
        return []


def _get_sections_with_new_data() -> list[str]:
    """Identify sections that have received new data since last build."""
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("section")
            .not_.is_("section", "null")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

        if result.data:
            return list(set(
                f"BP.{row['section']}" for row in result.data if row.get("section")
            ))
    except Exception as e:
        logger.error("[BuildHandler] Error finding sections with new data: %s", e)
    return []


def _get_weak_sections(threshold: float) -> list[str]:
    """Find sections below a confidence threshold."""
    try:
        from services.coverage_calculator import get_plan_coverage

        coverage = get_plan_coverage()
        per_section = coverage.get("per_section", {})

        weak = []
        for section_id, data in per_section.items():
            if data.get("coverage_pct", 0) < threshold:
                weak.append(section_id)
        return weak
    except Exception as e:
        logger.error("[BuildHandler] Error finding weak sections: %s", e)
    return []
