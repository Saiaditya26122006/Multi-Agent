"""
BUILD Workspace Handler — orchestrates business plan generation.

Handles: full plan builds, single section builds, incremental rebuilds,
weak-section-only builds. Reports progress and surfaces blockers.
"""

import logging
from typing import Optional

from tools.trace_emitter import emit_trace
from services.pipeline_orchestrator import get_orchestrator

logger = logging.getLogger(__name__)


def _trace(session_id: Optional[str], step: str, detail: str, data: Optional[dict] = None) -> None:
    """Emit a trace event only if we actually have a session to broadcast to."""
    if session_id:
        emit_trace(session_id, "Build", step, detail, data or {})


def build_full_plan(session_id: Optional[str] = None) -> dict:
    """Trigger the full pipeline via Pipeline Orchestrator.

    Args:
        session_id: Current session identifier.

    Returns:
        Dict with: status, run_id, message
    """
    try:
        _trace(session_id, "checking_blockers", "Checking which sections are blocked by missing data...")
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

        # Trigger pipeline via orchestrator
        orchestrator = get_orchestrator()
        result = orchestrator.start_build(
            session_id=session_id,
            instruction="Build the full business plan",
            scope="all"
        )

        if "error" in result:
            return {
                "status": "error",
                "message": result["error"]
            }

        return {
            "status": "started",
            "run_id": result["run_id"],
            "message": "Pipeline started — watch the Activity Drawer for real-time progress"
        }

    except Exception as e:
        logger.exception("[BuildHandler] build_full_plan failed")
        return {
            "status": "error",
            "message": f"Failed to start pipeline: {str(e)}"
        }


# Keep the old function for reference
def _old_build_full_plan_stub(session_id: Optional[str] = None) -> dict:
    """OLD STUB — kept for reference during migration."""
    try:
        _trace(session_id, "checking_blockers", "Checking which sections are blocked by missing data...")
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

        sections = _get_all_sections()
        _trace(session_id, "queued", f"{len(sections)} section(s) queued, no blockers found")
        return {
            "status": "started",
            "message": "Full plan build initiated. All sections will be generated.",
            "sections_queued": sections,
        }
    except Exception as e:
        logger.error("[BuildHandler] Error in build_full_plan: %s", e)
        return {
            "status": "error",
            "message": "I couldn't process that right now. Try again.",
            "error_type": type(e).__name__,
        }


def build_section(section_id: str, session_id: Optional[str] = None) -> dict:
    """Trigger a single section build via Pipeline Orchestrator.

    Args:
        section_id: The BP section to build (e.g. "BP.9", "9").
        session_id: Current session identifier.

    Returns:
        Dict with: status, message, section, run_id, blockers.
    """
    normalized = _normalize_section_id(section_id)
    _trace(session_id, "checking_section_blockers", f"Checking whether {normalized} has unmet dependencies...")
    blockers = _get_section_blockers(normalized)

    if blockers:
        _trace(session_id, "section_blocked", f"{normalized} blocked by: {', '.join(blockers)}")
        return {
            "status": "blocked",
            "message": f"Section {normalized} is blocked by: {', '.join(blockers)}",
            "section": normalized,
            "blockers": blockers,
        }

    # Trigger single-section pipeline
    orchestrator = get_orchestrator()
    result = orchestrator.start_build(
        session_id=session_id,
        instruction=f"Build section {normalized}",
        scope=normalized
    )

    if "error" in result:
        return {
            "status": "error",
            "message": result["error"],
            "section": normalized,
        }

    return {
        "status": "started",
        "message": f"Building section {normalized} — watch Activity Drawer for progress",
        "section": normalized,
        "run_id": result["run_id"],
    }


def build_incremental(session_id: Optional[str] = None) -> dict:
    """Rebuild only sections with new data since last build.

    Args:
        session_id: Current session identifier.

    Returns:
        Dict with: status, sections_to_rebuild, reason.
    """
    _trace(session_id, "scanning_new_data", "Scanning for sections with new data since the last build...")
    sections_with_new_data = _get_sections_with_new_data()

    if not sections_with_new_data:
        return {
            "status": "nothing_to_build",
            "message": "No sections have new data since the last build. Feed more data first.",
            "sections_to_rebuild": [],
        }

    _trace(session_id, "incremental_queued", f"{len(sections_with_new_data)} section(s) have new data")
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
    _trace(session_id, "scanning_weak", f"Scanning for sections below {threshold}% confidence...")
    weak = _get_weak_sections(threshold)

    if not weak:
        return {
            "status": "nothing_to_build",
            "message": f"All sections are above {threshold}% confidence. Nothing to rebuild.",
            "weak_sections": [],
        }

    _trace(session_id, "weak_queued", f"{len(weak)} section(s) below threshold")
    return {
        "status": "started",
        "message": f"Rebuilding {len(weak)} section(s) below {threshold}% confidence.",
        "weak_sections": weak,
        "threshold": threshold,
    }


def get_build_status(session_id: Optional[str] = None) -> dict:
    """Get the current pipeline build status from orchestrator.

    Args:
        session_id: Current session identifier.

    Returns:
        Dict with: running (bool), status, progress, current_group, run_id.
    """
    if not session_id:
        return {
            "running": False,
            "status": "idle",
            "progress": 0,
        }

    orchestrator = get_orchestrator()
    status = orchestrator.get_status(session_id)

    is_running = status.get("status") in ("building", "waiting_for_alex")
    current_group = status.get("current_group", 0)

    # Estimate progress based on group (4 groups total)
    progress = 0
    if current_group > 0:
        progress = int((current_group / 4) * 100)

    return {
        "running": is_running,
        "status": status.get("status", "idle"),
        "progress": progress,
        "current_group": current_group,
        "run_id": status.get("run_id"),
        "started_at": status.get("started_at"),
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
            # build_full_plan's blockers are dicts ({section_id, blocked_by});
            # build_section's are plain strings naming what's missing. Handle both.
            if isinstance(b, dict):
                section = b.get("section_id", result.get("section", "?"))
                blocked_by = ", ".join(b.get("blocked_by", []))
                lines.append(f"  - {section} needs: {blocked_by}")
            else:
                lines.append(f"  - {b}")
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
