"""
INSPECT Workspace Handler — analyzes plan state and surfaces insights.

Handles: coverage heatmap, confidence breakdown, contradictions,
stale data, dependency chains, section deep-dives.
"""

import logging
from typing import Optional

from tools.trace_emitter import emit_trace

logger = logging.getLogger(__name__)


def _trace(session_id: Optional[str], step: str, detail: str, data: Optional[dict] = None) -> None:
    """Emit a trace event only if we actually have a session to broadcast to."""
    if session_id:
        emit_trace(session_id, "Inspect", step, detail, data or {})


def get_coverage_heatmap(session_id: Optional[str] = None) -> dict:
    """Get section-by-section coverage for panel visualization.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with sections list, each having section_id, title, coverage_pct, status.
    """
    try:
        from services.coverage_calculator import get_plan_coverage

        _trace(session_id, "computing_coverage", "Calculating coverage across all plan sections...")
        coverage = get_plan_coverage()
        per_section = coverage.get("per_section", {})

        sections = []
        for section_id, data in sorted(per_section.items()):
            pct = data.get("coverage_pct", 0)
            if pct >= 80:
                status = "strong"
            elif pct >= 50:
                status = "moderate"
            elif pct > 0:
                status = "weak"
            else:
                status = "empty"

            sections.append({
                "section_id": section_id,
                "title": data.get("title", ""),
                "total_nodes": data.get("total_nodes", 0),
                "filled_nodes": data.get("filled_nodes", 0),
                "coverage_pct": pct,
                "status": status,
            })

        _trace(
            session_id, "coverage_complete",
            f"Coverage computed — {coverage.get('coverage_pct', 0):.0f}% overall across {len(sections)} section(s)",
        )

        return {
            "overall_coverage_pct": coverage.get("coverage_pct", 0),
            "total_nodes": coverage.get("total_nodes", 0),
            "filled_nodes": coverage.get("filled_nodes", 0),
            "sections": sections,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting coverage: %s", e)
        return {"overall_coverage_pct": 0, "sections": []}


def get_confidence_breakdown(session_id: Optional[str] = None) -> dict:
    """Get per-section CONFIRMED vs ASSUMPTION vs INFERRED split.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with breakdown counts and percentages.
    """
    try:
        from services.coverage_calculator import get_confidence_breakdown as calc_confidence

        _trace(session_id, "computing_confidence", "Breaking down data by epistemic status...")
        result = calc_confidence()
        _trace(
            session_id, "confidence_complete",
            f"Confidence breakdown ready — {result.get('confidence_pct', 0):.0f}% CONFIRMED",
        )
        return result
    except Exception as e:
        logger.error("[InspectHandler] Error getting confidence: %s", e)
        return {"breakdown": {}, "confidence_pct": 0}


def get_contradictions_list(session_id: Optional[str] = None) -> dict:
    """Get all unresolved contradictions with details.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with count and list of contradiction details.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        # Only rows explicitly tagged epistemic_status="CONTRADICTION" with
        # no superseded_by represent a genuinely open issue. We deliberately
        # do NOT scan "contradiction_resolution" chunks here — those are
        # only ever written after a contradiction is resolved (see
        # services.rag_hooks.store_contradiction_resolution), so every one
        # of them already represents a closed issue, not an open one. An
        # earlier version of this function checked for a "resolved"
        # metadata flag on those chunks that no code ever actually sets,
        # which meant every past resolution got double-counted as a brand
        # new open contradiction (inflated the count by ~25x in practice).
        _trace(session_id, "checking_flagged", "Checking for explicitly flagged CONTRADICTION records...")
        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id, content, section, created_at")
            .eq("epistemic_status", "CONTRADICTION")
            .is_("superseded_by", "null")
            .execute()
        )

        contradictions = []
        if result.data:
            for row in result.data:
                contradictions.append({
                    "id": row["id"],
                    "content": row["content"],
                    "section": row.get("section"),
                    "created_at": row.get("created_at"),
                })

        _trace(
            session_id, "contradictions_complete",
            f"Found {len(contradictions)} unresolved contradiction(s)",
        )

        return {
            "count": len(contradictions),
            "contradictions": contradictions,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting contradictions: %s", e)
        return {"count": 0, "contradictions": []}


def get_stale_data_report(max_age_days: int = 30, session_id: Optional[str] = None) -> dict:
    """Get items needing refresh, ranked by staleness.

    Args:
        max_age_days: Threshold for staleness.
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with count and list of stale items.
    """
    try:
        from services.coverage_calculator import get_stale_items

        _trace(session_id, "scanning_staleness", f"Scanning for data older than {max_age_days} days...")
        stale = get_stale_items(max_age_days=max_age_days)
        _trace(session_id, "staleness_complete", f"Found {len(stale)} stale item(s)")
        return {
            "count": len(stale),
            "max_age_threshold": max_age_days,
            "items": stale,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting stale data: %s", e)
        return {"count": 0, "items": []}


def get_dependency_view(session_id: Optional[str] = None) -> dict:
    """Get the dependency chain visualization data.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with nodes and edges for the dependency graph.
    """
    try:
        from services.coverage_calculator import get_blocked_sections, get_sections

        _trace(session_id, "mapping_dependencies", "Mapping section dependencies and blockers...")
        sections = get_sections()
        blocked = get_blocked_sections()

        blocked_map = {b["section_id"]: b["blocked_by"] for b in blocked}

        nodes = []
        edges = []

        for section_id, info in sorted(sections.items()):
            status = "blocked" if section_id in blocked_map else "ready"
            nodes.append({
                "id": section_id,
                "title": info.get("title", ""),
                "status": status,
            })

            if section_id in blocked_map:
                for dep in blocked_map[section_id]:
                    edges.append({
                        "from": dep,
                        "to": section_id,
                        "type": "blocks",
                    })

        _trace(
            session_id, "dependencies_complete",
            f"Mapped {len(nodes)} section(s), {len(blocked)} blocked",
        )

        return {
            "nodes": nodes,
            "edges": edges,
            "blocked_count": len(blocked),
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting dependency view: %s", e)
        return {"nodes": [], "edges": [], "blocked_count": 0}


def get_section_deep_dive(section_id: str, session_id: Optional[str] = None) -> dict:
    """Deep dive into a specific section: all nodes, status, data, ages.

    Args:
        section_id: The section to inspect (e.g., "BP.9" or "9").
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with section details, nodes, and their data.
    """
    if not section_id.startswith("BP."):
        section_id = f"BP.{section_id}"

    try:
        from services.coverage_calculator import _load_bp_architecture
        from services.rag_service import retrieve

        arch = _load_bp_architecture()
        nodes = arch.get("nodes", [])

        section_nodes = [
            n for n in nodes
            if n.get("node_id", "").startswith(section_id)
        ]

        _trace(
            session_id, "deep_dive_start",
            f"Deep-diving section {section_id} — checking {len(section_nodes)} node(s) for data...",
        )

        node_details = []
        for node in section_nodes:
            node_id = node.get("node_id", "")
            title = node.get("node_title", node.get("purpose", ""))

            chunks = retrieve(
                query=title or node_id,
                source_types=["ceo_doc", "conversation", "decision"],
                top_k=2,
                threshold=0.4,
            )

            node_details.append({
                "node_id": node_id,
                "title": title,
                "has_data": len(chunks) > 0,
                "data_count": len(chunks),
                "top_data": chunks[0].content[:100] if chunks else None,
                "epistemic_status": chunks[0].epistemic_status if chunks else None,
                "prohibited_claims": node.get("prohibited_claims", ""),
            })

        filled = sum(1 for n in node_details if n["has_data"])
        total = len(node_details)

        _trace(
            session_id, "deep_dive_complete",
            f"Section {section_id}: {filled}/{total} node(s) filled",
        )

        return {
            "section_id": section_id,
            "total_nodes": total,
            "filled_nodes": filled,
            "coverage_pct": round((filled / total * 100) if total else 0, 1),
            "nodes": node_details,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error in section deep dive: %s", e)
        return {"section_id": section_id, "total_nodes": 0, "nodes": []}


def answer_inspect_question(question: str, session_id: Optional[str] = None) -> dict:
    """Answer a free-form inspection question using RAG + LLM synthesis.

    Args:
        question: Alex's natural language question about plan state.
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with answer text and supporting sources.
    """
    try:
        from services.rag_service import retrieve
        from web.handlers.llm_helper import generate_answer

        _trace(session_id, "searching_kb", f"Searching the knowledge base for: \"{question[:60]}\"...")
        chunks = retrieve(
            query=question,
            top_k=10,
            threshold=0.38,
        )

        filtered = [
            c for c in chunks
            if c.source_type not in ("conversation",)
            and "unique_retrieval_test" not in (c.content or "")
        ]

        if not filtered:
            _trace(session_id, "no_results", "No relevant data found in the knowledge base")
            return {
                "answer": "No relevant data found in the knowledge base for that question.",
                "sources": [],
            }

        _trace(
            session_id, "synthesizing",
            f"Found {len(filtered)} relevant fact(s) — asking the model to synthesize an answer...",
        )
        answer = generate_answer(question, filtered[:5])

        sources = [
            {
                "content": c.content[:200],
                "source_type": c.source_type,
                "section": c.section,
                "epistemic_status": c.epistemic_status,
                "similarity": round(c.similarity, 3),
            }
            for c in filtered[:5]
        ]

        _trace(session_id, "answer_ready", "Answer ready", {"source_count": len(sources)})

        return {
            "answer": answer,
            "sources": sources,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error answering question: %s", e)
        return {"answer": "Error retrieving data.", "sources": []}


def format_inspect_response(data: dict, query_type: str) -> str:
    """Format inspect results as a chat message.

    Args:
        data: Output from any inspect function.
        query_type: Type of query (coverage/confidence/contradictions/stale/dependencies/section).

    Returns:
        Formatted string for chat.
    """
    if query_type == "coverage":
        sections = data.get("sections", [])
        overall = data.get("overall_coverage_pct", 0)
        lines = [f"Overall coverage: {overall:.0f}%"]
        lines.append("")
        for s in sections:
            bar_filled = int(s["coverage_pct"] / 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)
            status_icon = "🔴" if s["status"] == "empty" else ("🟡" if s["status"] == "weak" else "")
            lines.append(
                f"  {s['section_id']}: {bar} {s['coverage_pct']:.0f}% "
                f"({s['filled_nodes']}/{s['total_nodes']}) {status_icon}"
            )
        return "\n".join(lines)

    if query_type == "confidence":
        breakdown = data.get("breakdown", {})
        pct = data.get("confidence_pct", 0)
        lines = [f"Confidence: {pct:.0f}% of tagged data is CONFIRMED"]
        lines.append("")
        for status, count in sorted(breakdown.items()):
            lines.append(f"  {status}: {count}")
        return "\n".join(lines)

    if query_type == "contradictions":
        count = data.get("count", 0)
        items = data.get("contradictions", [])
        if count == 0:
            return "No unresolved contradictions found."
        lines = [f"{count} unresolved contradiction(s):"]
        lines.append("")
        for i, c in enumerate(items[:10], 1):
            lines.append(f"  {i}. {c['content'][:80]}...")
        return "\n".join(lines)

    if query_type == "stale":
        count = data.get("count", 0)
        items = data.get("items", [])
        if count == 0:
            return "No stale data found. Everything is fresh."
        lines = [f"{count} stale item(s) (>{data.get('max_age_threshold', 30)} days):"]
        lines.append("")
        for item in items[:10]:
            lines.append(f"  - [{item['age_days']}d] {item['content_preview'][:60]}...")
        return "\n".join(lines)

    if query_type == "section":
        section = data.get("section_id", "?")
        nodes = data.get("nodes", [])
        filled = data.get("filled_nodes", 0)
        total = data.get("total_nodes", 0)
        lines = [f"Section {section}: {filled}/{total} nodes filled ({data.get('coverage_pct', 0):.0f}%)"]
        lines.append("")
        for n in nodes:
            icon = "✓" if n["has_data"] else "✗"
            status = f" [{n['epistemic_status']}]" if n.get("epistemic_status") else ""
            lines.append(f"  {icon} {n['node_id']} — {n['title'][:50]}{status}")
        return "\n".join(lines)

    if query_type == "question":
        return data.get("answer", "No data found.")

    if query_type == "dependencies":
        # get_dependency_view() actually returns {"nodes", "edges",
        # "blocked_count"} — there is no "chains" key. Reading data.get
        # ("chains", []) always returned an empty list, so this command
        # showed "No dependency chains found." unconditionally, even when
        # sections were genuinely blocked.
        edges = data.get("edges", [])
        blocked_count = data.get("blocked_count", 0)
        if not edges:
            return "No dependency chains found — nothing is currently blocked by a missing upstream section."
        lines = [f"Dependency chains — {blocked_count} section(s) blocked:"]
        lines.append("")
        for edge in edges[:15]:
            lines.append(f"  {edge.get('from', '?')} → {edge.get('to', '?')} ({edge.get('type', 'blocks')})")
        if len(edges) > 15:
            lines.append(f"\n  ...and {len(edges) - 15} more.")
        return "\n".join(lines)

    return str(data)
