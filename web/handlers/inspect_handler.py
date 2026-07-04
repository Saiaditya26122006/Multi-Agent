"""
INSPECT Workspace Handler — analyzes plan state and surfaces insights.

Handles: coverage heatmap, confidence breakdown, contradictions,
stale data, dependency chains, section deep-dives.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_coverage_heatmap() -> dict:
    """Get section-by-section coverage for panel visualization.

    Returns:
        Dict with sections list, each having section_id, title, coverage_pct, status.
    """
    try:
        from services.coverage_calculator import get_plan_coverage

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

        return {
            "overall_coverage_pct": coverage.get("coverage_pct", 0),
            "total_nodes": coverage.get("total_nodes", 0),
            "filled_nodes": coverage.get("filled_nodes", 0),
            "sections": sections,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting coverage: %s", e)
        return {"overall_coverage_pct": 0, "sections": []}


def get_confidence_breakdown() -> dict:
    """Get per-section CONFIRMED vs ASSUMPTION vs INFERRED split.

    Returns:
        Dict with breakdown counts and percentages.
    """
    try:
        from services.coverage_calculator import get_confidence_breakdown as calc_confidence

        return calc_confidence()
    except Exception as e:
        logger.error("[InspectHandler] Error getting confidence: %s", e)
        return {"breakdown": {}, "confidence_pct": 0}


def get_contradictions_list() -> dict:
    """Get all unresolved contradictions with details.

    Returns:
        Dict with count and list of contradiction details.
    """
    try:
        from services.rag_service import retrieve

        chunks = retrieve(
            query="contradiction conflict inconsistency disagreement",
            source_types=["contradiction_resolution"],
            top_k=50,
            threshold=0.3,
        )

        contradictions = []
        for chunk in chunks:
            if chunk.metadata.get("resolved") is not True:
                contradictions.append({
                    "id": chunk.id,
                    "content": chunk.content,
                    "section": chunk.section,
                    "created_at": chunk.created_at,
                })

        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id, content, section, created_at")
            .eq("epistemic_status", "CONTRADICTION")
            .is_("superseded_by", "null")
            .execute()
        )

        if result.data:
            for row in result.data:
                if not any(c["id"] == row["id"] for c in contradictions):
                    contradictions.append({
                        "id": row["id"],
                        "content": row["content"],
                        "section": row.get("section"),
                        "created_at": row.get("created_at"),
                    })

        return {
            "count": len(contradictions),
            "contradictions": contradictions,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting contradictions: %s", e)
        return {"count": 0, "contradictions": []}


def get_stale_data_report(max_age_days: int = 30) -> dict:
    """Get items needing refresh, ranked by staleness.

    Args:
        max_age_days: Threshold for staleness.

    Returns:
        Dict with count and list of stale items.
    """
    try:
        from services.coverage_calculator import get_stale_items

        stale = get_stale_items(max_age_days=max_age_days)
        return {
            "count": len(stale),
            "max_age_threshold": max_age_days,
            "items": stale,
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting stale data: %s", e)
        return {"count": 0, "items": []}


def get_dependency_view() -> dict:
    """Get the dependency chain visualization data.

    Returns:
        Dict with nodes and edges for the dependency graph.
    """
    try:
        from services.coverage_calculator import get_blocked_sections, get_sections

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

        return {
            "nodes": nodes,
            "edges": edges,
            "blocked_count": len(blocked),
        }
    except Exception as e:
        logger.error("[InspectHandler] Error getting dependency view: %s", e)
        return {"nodes": [], "edges": [], "blocked_count": 0}


def get_section_deep_dive(section_id: str) -> dict:
    """Deep dive into a specific section: all nodes, status, data, ages.

    Args:
        section_id: The section to inspect (e.g., "BP.9" or "9").

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


def answer_inspect_question(question: str) -> dict:
    """Answer a free-form inspection question using RAG + LLM synthesis.

    Args:
        question: Alex's natural language question about plan state.

    Returns:
        Dict with answer text and supporting sources.
    """
    try:
        from services.rag_service import retrieve
        from web.handlers.llm_helper import generate_answer

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
            return {
                "answer": "No relevant data found in the knowledge base for that question.",
                "sources": [],
            }

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
        chains = data.get("chains", [])
        if not chains:
            return "No dependency chains found."
        lines = ["Dependency chains:"]
        lines.append("")
        for chain in chains[:10]:
            lines.append(f"  {chain.get('from', '?')} → {chain.get('to', '?')}: {chain.get('reason', '')}")
        return "\n".join(lines)

    return str(data)
