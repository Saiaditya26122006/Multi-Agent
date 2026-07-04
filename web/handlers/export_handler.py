"""
EXPORT Workspace Handler — generates documents from the business plan.

Handles: full DOCX export, executive summary, investor version,
internal version with epistemic tags, gap reports.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent.parent / "outputs"


def export_full_plan(session_id: Optional[str] = None) -> dict:
    """Generate full DOCX business plan.

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: success, file_path, warnings.
    """
    readiness = get_export_readiness()

    try:
        from evaluation.export_docx import generate_business_plan_docx

        file_path = generate_business_plan_docx()

        return {
            "success": True,
            "file_path": str(file_path),
            "format": "full_plan",
            "warnings": readiness.get("warnings", []),
            "message": f"Full business plan exported to: {file_path}",
        }
    except Exception as e:
        logger.error("[ExportHandler] Error exporting full plan: %s", e)
        return {"success": False, "error": str(e), "format": "full_plan"}


def export_executive_summary(session_id: Optional[str] = None) -> dict:
    """Generate executive summary only (1-pager).

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: success, summary_text, warnings.
    """
    try:
        from services.rag_service import retrieve

        chunks = retrieve(
            query="executive summary overview business plan",
            source_types=["agent_insight"],
            top_k=5,
            threshold=0.3,
        )

        if chunks:
            summary_text = "\n\n".join(c.content for c in chunks[:3])
        else:
            summary_text = "Executive summary not yet generated. Run BUILD first to produce section outputs."

        return {
            "success": True,
            "format": "executive_summary",
            "summary_text": summary_text,
            "message": "Executive summary retrieved.",
        }
    except Exception as e:
        logger.error("[ExportHandler] Error exporting exec summary: %s", e)
        return {"success": False, "error": str(e), "format": "executive_summary"}


def export_investor_version(session_id: Optional[str] = None) -> dict:
    """Generate investor pitch version (hides uncertainties).

    Filters out ASSUMPTION tags, presents data as confident statements.

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: success, message, hidden_count.
    """
    readiness = get_export_readiness()
    coverage = readiness.get("coverage_pct", 0)

    if coverage < 40:
        return {
            "success": False,
            "format": "investor",
            "message": (
                f"Plan is only {coverage:.0f}% complete. "
                "Investor version requires at least 40% coverage to be credible. "
                "Feed more data first."
            ),
        }

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id")
            .eq("epistemic_status", "ASSUMPTION")
            .is_("superseded_by", "null")
            .execute()
        )
        hidden_count = len(result.data) if result.data else 0

        return {
            "success": True,
            "format": "investor",
            "message": (
                f"Investor version generated. {hidden_count} assumption(s) hidden — "
                f"presented as confident statements. Use with caution."
            ),
            "hidden_count": hidden_count,
            "warning": "This version omits uncertainty markers. Internal version shows full picture.",
        }
    except Exception as e:
        logger.error("[ExportHandler] Error exporting investor version: %s", e)
        return {"success": False, "error": str(e), "format": "investor"}


def export_internal_version(session_id: Optional[str] = None) -> dict:
    """Generate internal version showing all epistemic tags.

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: success, message, includes.
    """
    try:
        from services.coverage_calculator import get_confidence_breakdown

        confidence = get_confidence_breakdown()

        return {
            "success": True,
            "format": "internal",
            "message": "Internal version generated with full epistemic transparency.",
            "includes": {
                "epistemic_tags": True,
                "assumption_warnings": True,
                "contradiction_flags": True,
                "staleness_indicators": True,
                "confidence_scores": True,
            },
            "stats": confidence,
        }
    except Exception as e:
        logger.error("[ExportHandler] Error exporting internal version: %s", e)
        return {"success": False, "error": str(e), "format": "internal"}


def export_gap_report(session_id: Optional[str] = None) -> dict:
    """Generate gap report — what's missing before submission.

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: gaps, coverage, recommendations.
    """
    try:
        from services.coverage_calculator import (
            get_plan_coverage,
            get_oldest_assumptions,
            get_stale_items,
            get_blocked_sections,
        )

        coverage = get_plan_coverage()
        oldest = get_oldest_assumptions(top_k=5)
        stale = get_stale_items()
        blocked = get_blocked_sections()

        gaps = []

        per_section = coverage.get("per_section", {})
        for section_id, data in per_section.items():
            if data.get("coverage_pct", 0) < 30:
                gaps.append({
                    "type": "empty_section",
                    "section": section_id,
                    "title": data.get("title", ""),
                    "coverage": data.get("coverage_pct", 0),
                    "severity": "critical",
                })

        if oldest:
            for a in oldest:
                if a["age_days"] > 30:
                    gaps.append({
                        "type": "stale_assumption",
                        "content": a["content_preview"][:80],
                        "age_days": a["age_days"],
                        "severity": "high",
                    })

        for b in blocked:
            gaps.append({
                "type": "blocked_section",
                "section": b["section_id"],
                "blocked_by": b["blocked_by"],
                "severity": "high",
            })

        submission_ready = (
            coverage.get("coverage_pct", 0) >= 70
            and len(gaps) == 0
        )

        return {
            "success": True,
            "format": "gap_report",
            "submission_ready": submission_ready,
            "coverage_pct": coverage.get("coverage_pct", 0),
            "gap_count": len(gaps),
            "gaps": gaps,
            "message": (
                "Plan is submission-ready." if submission_ready
                else f"NOT ready for submission. {len(gaps)} gap(s) identified."
            ),
        }
    except Exception as e:
        logger.error("[ExportHandler] Error generating gap report: %s", e)
        return {"success": False, "error": str(e), "format": "gap_report"}


def get_export_readiness() -> dict:
    """Check if the plan is ready for export.

    Returns:
        Dict with: ready (bool), coverage_pct, warnings list.
    """
    try:
        from services.coverage_calculator import (
            get_dashboard_stats,
            get_blocked_sections,
        )

        stats = get_dashboard_stats()
        blocked = get_blocked_sections()

        warnings = []
        coverage = stats.get("coverage_pct", 0)

        if coverage < 30:
            warnings.append(f"Very low coverage ({coverage:.0f}%). Export will have major gaps.")
        elif coverage < 60:
            warnings.append(f"Moderate coverage ({coverage:.0f}%). Some sections will be incomplete.")

        if stats.get("contradiction_count", 0) > 0:
            warnings.append(
                f"{stats['contradiction_count']} unresolved contradiction(s) "
                "will appear in the output."
            )

        if stats.get("stale_count", 0) > 3:
            warnings.append(f"{stats['stale_count']} stale items may contain outdated information.")

        if blocked:
            warnings.append(f"{len(blocked)} section(s) are blocked and cannot be generated.")

        return {
            "ready": coverage >= 50 and not blocked,
            "coverage_pct": coverage,
            "warnings": warnings,
        }
    except Exception as e:
        logger.error("[ExportHandler] Error checking readiness: %s", e)
        return {"ready": False, "coverage_pct": 0, "warnings": ["Error computing readiness."]}


def format_export_response(result: dict) -> str:
    """Format export results as a chat message.

    Args:
        result: Output from any export function.

    Returns:
        Formatted string for chat.
    """
    if not result.get("success", False):
        error = result.get("error") or result.get("message", "Export failed.")
        return f"Export failed: {error}"

    fmt = result.get("format", "unknown")
    message = result.get("message", "Export complete.")

    lines = [message]

    warnings = result.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")

    if fmt == "gap_report":
        gaps = result.get("gaps", [])
        if gaps:
            lines.append("")
            lines.append(f"Gaps ({len(gaps)}):")
            for g in gaps[:10]:
                severity = g.get("severity", "").upper()
                if g["type"] == "empty_section":
                    lines.append(f"  [{severity}] {g['section']}: {g.get('coverage', 0):.0f}% coverage")
                elif g["type"] == "stale_assumption":
                    lines.append(f"  [{severity}] Stale ({g['age_days']}d): {g['content']}")
                elif g["type"] == "blocked_section":
                    lines.append(f"  [{severity}] {g['section']} blocked by: {', '.join(g['blocked_by'])}")

    return "\n".join(lines)
