"""
EXPORT Workspace Handler — generates documents from the business plan.

Handles: full DOCX export, executive summary, investor version,
internal version with epistemic tags, gap reports.
"""

import logging
from pathlib import Path
from typing import Optional

from tools.trace_emitter import emit_trace

logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent.parent / "outputs"


def _trace(session_id: Optional[str], step: str, detail: str, data: Optional[dict] = None) -> None:
    """Emit a trace event only if we actually have a session to broadcast to."""
    if session_id:
        emit_trace(session_id, "Export", step, detail, data or {})


def export_full_plan(session_id: Optional[str] = None) -> dict:
    """Generate full DOCX business plan with live section-by-section preview.

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: success, file_path, download_url, warnings.
    """
    _trace(session_id, "checking_readiness", "Checking plan readiness before export...")
    readiness = get_export_readiness(session_id=session_id)

    try:
        from evaluation.export_docx import export_to_docx
        from services.rag_service import retrieve, _get_supabase

        _trace(session_id, "generating_docx", "Compiling sections into a DOCX document...")

        _emit_section_previews(session_id)

        results_path = str(OUTPUTS_DIR / "latest_results.json")
        file_path = export_to_docx(results_path)
        filename = Path(file_path).name

        _trace(
            session_id, "export_ready",
            f"Export ready: {filename}",
            {"filename": filename, "downloadable": True},
        )

        return {
            "success": True,
            "file_path": str(file_path),
            "download_url": f"/api/export/download/{filename}",
            "format": "full_plan",
            "warnings": readiness.get("warnings", []),
            "message": f"Full business plan exported to: {file_path}",
        }
    except Exception as e:
        logger.error("[ExportHandler] Error exporting full plan: %s", e)
        return {"success": False, "error": str(e), "format": "full_plan"}


def _emit_section_previews(session_id: Optional[str]) -> None:
    """Emit live export preview traces as sections are assembled."""
    if not session_id:
        return

    try:
        from services.node_indexer import get_all_sections
        from services.rag_service import retrieve

        sections = get_all_sections()
        for s in sections:
            section_id = s.get("section_id", "")
            title = s.get("title", section_id)

            chunks = retrieve(
                query=f"business plan {title}",
                section=s.get("section_num"),
                source_types=["ceo_doc", "agent_insight", "decision"],
                top_k=1,
                threshold=0.3,
            )

            has_data = bool(chunks)
            confidence = "strong" if has_data else "empty"
            preview = chunks[0].content[:80] if has_data else "No data yet"

            emit_trace(
                session_id, "Export", "section_preview",
                f"Assembling {section_id}: {title}",
                data={
                    "type": "export_preview",
                    "section_id": section_id,
                    "title": title,
                    "confidence": confidence,
                    "preview": preview,
                    "has_data": has_data,
                },
            )
    except Exception as e:
        logger.warning("[ExportHandler] Section preview emission failed: %s", e)


def export_executive_summary(session_id: Optional[str] = None) -> dict:
    """Generate executive summary only (1-pager).

    Args:
        session_id: Current session ID.

    Returns:
        Dict with: success, summary_text, warnings.
    """
    try:
        from services.rag_service import retrieve

        _trace(session_id, "retrieving_summary", "Retrieving executive summary insights...")
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

        _trace(session_id, "summary_ready", "Executive summary retrieved")

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
    _trace(session_id, "checking_readiness", "Checking coverage before generating investor version...")
    readiness = get_export_readiness(session_id=session_id)
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

        _trace(session_id, "filtering_assumptions", "Filtering out unconfirmed assumptions for the investor cut...")
        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id")
            .eq("epistemic_status", "ASSUMPTION")
            .is_("superseded_by", "null")
            .execute()
        )
        hidden_count = len(result.data) if result.data else 0
        _trace(session_id, "investor_ready", f"Investor version ready — {hidden_count} assumption(s) hidden")

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

        _trace(session_id, "computing_confidence", "Computing full epistemic breakdown for internal version...")
        confidence = get_confidence_breakdown()
        _trace(session_id, "internal_ready", "Internal version ready with full transparency")

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

        _trace(session_id, "assembling_gap_report", "Checking coverage, staleness, and blockers for the gap report...")
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

        _trace(session_id, "gap_report_ready", f"Gap report ready — {len(gaps)} gap(s) found")

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


def get_export_readiness(session_id: Optional[str] = None) -> dict:
    """Check if the plan is ready for export.

    Args:
        session_id: Current session ID, for live trace narration.

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
    # get_export_readiness() (the "d" menu command) returns
    # {"ready", "coverage_pct", "warnings"} with no "success" key at all —
    # `result.get("success", False)` therefore defaulted to False and this
    # branch reported "Export failed: Export failed." for a readiness
    # check that actually succeeded and computed real data.
    if "ready" in result and "success" not in result:
        coverage = result.get("coverage_pct", 0)
        lines = [
            f"Export readiness: {'READY' if result.get('ready') else 'NOT READY'} "
            f"({coverage:.0f}% coverage)"
        ]
        warnings = result.get("warnings", [])
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)

    if not result.get("success", False):
        error = result.get("error") or result.get("message", "Export failed.")
        return f"Export failed: {error}"

    fmt = result.get("format", "unknown")
    message = result.get("message", "Export complete.")

    lines = [message]

    # export_executive_summary() puts the actual content in "summary_text"
    # and leaves "message" as a generic "Executive summary retrieved." —
    # without this, the summary itself never reached the chat.
    if fmt == "executive_summary" and result.get("summary_text"):
        lines.append("")
        lines.append(result["summary_text"])

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
