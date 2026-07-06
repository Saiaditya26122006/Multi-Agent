"""
Coverage Calculator — computes plan completeness, confidence, and gap metrics.

Queries the RAG knowledge base and bp_architecture to determine how much of
the business plan is filled, what's strong, what's weak, and what's stale.
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_dashboard_cache: dict = {"stats": None, "timestamp": 0.0}
_CACHE_TTL: int = 60

_bp_architecture = None
_bp_dependencies = None

BP_ARCHITECTURE_PATH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
BP_DEPENDENCIES_PATH = Path(__file__).parent.parent / "ceo_data" / "bp_dependencies.json"


def _load_bp_architecture() -> dict:
    """Load and cache the business plan architecture."""
    global _bp_architecture
    if _bp_architecture is None:
        with open(BP_ARCHITECTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _bp_architecture = data
    return _bp_architecture


def _load_bp_dependencies() -> dict:
    """Load and cache the dependency graph."""
    global _bp_dependencies
    if _bp_dependencies is None:
        with open(BP_DEPENDENCIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _bp_dependencies = data
    return _bp_dependencies


def _get_sections_from_nodes(nodes: list[dict]) -> dict[str, list[dict]]:
    """Group nodes by their top-level section (BP.1, BP.2, etc.)."""
    sections: dict[str, list[dict]] = {}
    for node in nodes:
        node_id = node.get("node_id", "")
        parts = node_id.split(".")
        if len(parts) >= 2:
            section_key = f"BP.{parts[1]}"
        else:
            section_key = node_id
        if section_key not in sections:
            sections[section_key] = []
        sections[section_key].append(node)
    return sections


def get_total_node_count() -> int:
    """Return the total number of nodes in the architecture."""
    arch = _load_bp_architecture()
    return len(arch.get("nodes", []))


def get_sections() -> dict[str, dict]:
    """Return section metadata: id, title, node count.

    Returns:
        Dict keyed by section ID (e.g. "BP.1") with title and node_count.
    """
    arch = _load_bp_architecture()
    nodes = arch.get("nodes", [])
    sections = _get_sections_from_nodes(nodes)

    result = {}
    for section_id, section_nodes in sorted(sections.items()):
        top_node = next(
            (n for n in section_nodes if n.get("node_id") == section_id), None
        )
        title = ""
        if top_node:
            title = top_node.get("node_title", "")
        result[section_id] = {
            "section_id": section_id,
            "title": title,
            "node_count": len(section_nodes),
        }
    return result


def get_plan_coverage() -> dict:
    """Calculate overall plan coverage metrics.

    Queries the RAG knowledge base to determine how many nodes have data mapped.

    Returns:
        Dict with: total_nodes, filled_nodes, coverage_pct, per_section breakdown.
    """
    arch = _load_bp_architecture()
    nodes = arch.get("nodes", [])
    total_nodes = len(nodes)
    sections = _get_sections_from_nodes(nodes)

    try:
        from services.rag_service import retrieve

        filled_nodes = set()
        per_section = {}

        for section_id, section_nodes in sorted(sections.items()):
            section_filled = 0
            for node in section_nodes:
                node_id = node.get("node_id", "")
                chunks = retrieve(
                    query=node.get("node_title", node_id),
                    source_types=["ceo_doc", "conversation", "decision", "correction"],
                    section=section_id.replace("BP.", ""),
                    # retrieve() fetches top_k * 3 raw candidates from the
                    # vector search, THEN applies the section/source_type
                    # filters in Python (rag_service.py's retrieve() —
                    # section isn't pushed into the SQL RPC call). With
                    # top_k=1 that's only 3 raw candidates per query, which
                    # is nowhere near enough for a section-specific match to
                    # survive the post-hoc filter out of ~1000 facts
                    # spanning many sections — this alone was enough to
                    # keep coverage at 0% regardless of the threshold value.
                    # top_k=8 gives the section filter a real pool (24 raw
                    # candidates) to search within; we still only care
                    # whether ANY chunk survives (`if chunks:` below), not
                    # how many.
                    top_k=8,
                    # 0.4 matches this project's documented embedding
                    # calibration (see CLAUDE.md: all-MiniLM-L6-v2 gives
                    # ~0.35-0.45 for related-but-differently-worded text).
                    # This was 0.5 before, which is above that ceiling and
                    # silently guaranteed near-zero matches regardless of
                    # how much real data existed — coverage always read 0%.
                    threshold=0.4,
                )
                if chunks:
                    filled_nodes.add(node_id)
                    section_filled += 1

            per_section[section_id] = {
                "section_id": section_id,
                "title": next(
                    (n.get("node_title", "") for n in section_nodes if n.get("node_id") == section_id),
                    "",
                ),
                "total_nodes": len(section_nodes),
                "filled_nodes": section_filled,
                "coverage_pct": round(
                    (section_filled / len(section_nodes) * 100) if section_nodes else 0, 1
                ),
            }

        overall_pct = round((len(filled_nodes) / total_nodes * 100) if total_nodes else 0, 1)

        return {
            "total_nodes": total_nodes,
            "filled_nodes": len(filled_nodes),
            "coverage_pct": overall_pct,
            "per_section": per_section,
        }
    except Exception as e:
        logger.error("[CoverageCalc] Error computing coverage: %s", e)
        return {
            "total_nodes": total_nodes,
            "filled_nodes": 0,
            "coverage_pct": 0.0,
            "per_section": {},
        }


def get_confidence_breakdown() -> dict:
    """Get breakdown of epistemic status across all stored knowledge.

    Returns:
        Dict with counts per status: CONFIRMED, ASSUMPTION, INFERRED, etc.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("epistemic_status")
            .not_.is_("epistemic_status", "null")
            .execute()
        )

        breakdown: dict[str, int] = {}
        total = 0
        if result.data:
            for row in result.data:
                status = row.get("epistemic_status", "UNKNOWN")
                breakdown[status] = breakdown.get(status, 0) + 1
                total += 1

        confirmed = breakdown.get("CONFIRMED", 0)
        confidence_pct = round((confirmed / total * 100) if total else 0, 1)

        return {
            "breakdown": breakdown,
            "total_tagged": total,
            "confirmed_count": confirmed,
            "confidence_pct": confidence_pct,
        }
    except Exception as e:
        logger.error("[CoverageCalc] Error computing confidence: %s", e)
        return {
            "breakdown": {},
            "total_tagged": 0,
            "confirmed_count": 0,
            "confidence_pct": 0.0,
        }


def get_contradiction_count() -> int:
    """Return the number of unresolved contradictions.

    NOTE: this only counts rows explicitly tagged epistemic_status=
    "CONTRADICTION" with no superseded_by set. It deliberately does NOT
    look at "contradiction_resolution" chunks — those are written by
    services.rag_hooks.store_contradiction_resolution() only AFTER a
    contradiction has been resolved, so every such record already
    represents a closed issue. There is no "resolved" metadata flag on
    them (nothing ever sets one), so treating their absence-of-flag as
    "still open" — which a previous version of this function did —
    silently counted every past resolution as a brand-new open issue.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id")
            .eq("epistemic_status", "CONTRADICTION")
            .is_("superseded_by", "null")
            .execute()
        )
        return len(result.data) if result.data else 0
    except Exception as e:
        logger.error("[CoverageCalc] Error counting contradictions: %s", e)
        return 0


def get_stale_items(max_age_days: int = 30) -> list[dict]:
    """Return items older than max_age_days that haven't been refreshed.

    Args:
        max_age_days: Threshold for staleness in days.

    Returns:
        List of dicts with id, content_preview, age_days, source_type.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

        result = (
            supabase.table(TABLE_NAME)
            .select("id, content, source_type, created_at")
            .lt("created_at", cutoff)
            .is_("superseded_by", "null")
            .limit(50)
            .execute()
        )

        stale = []
        if result.data:
            for row in result.data:
                created = datetime.fromisoformat(
                    row["created_at"].replace("Z", "+00:00")
                )
                age = (datetime.now(timezone.utc) - created).days
                stale.append({
                    "id": row["id"],
                    "content_preview": row["content"][:100],
                    "source_type": row["source_type"],
                    "age_days": age,
                })
        return sorted(stale, key=lambda x: x["age_days"], reverse=True)
    except Exception as e:
        logger.error("[CoverageCalc] Error finding stale items: %s", e)
        return []


def get_oldest_assumptions(top_k: int = 5) -> list[dict]:
    """Return the oldest unvalidated assumptions.

    Args:
        top_k: Number of assumptions to return.

    Returns:
        List of dicts with id, content_preview, age_days.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id, content, created_at")
            .eq("epistemic_status", "ASSUMPTION")
            .is_("superseded_by", "null")
            .order("created_at", desc=False)
            .limit(top_k)
            .execute()
        )

        assumptions = []
        if result.data:
            for row in result.data:
                created = datetime.fromisoformat(
                    row["created_at"].replace("Z", "+00:00")
                )
                age = (datetime.now(timezone.utc) - created).days
                assumptions.append({
                    "id": row["id"],
                    "content_preview": row["content"][:100],
                    "age_days": age,
                })
        return assumptions
    except Exception as e:
        logger.error("[CoverageCalc] Error finding oldest assumptions: %s", e)
        return []


def get_blocked_sections() -> list[dict]:
    """Return sections that cannot be built due to missing upstream dependencies.

    Returns:
        List of dicts with section_id, blocked_by (list of missing upstream sections).
    """
    deps_data = _load_bp_dependencies()
    dependencies = deps_data.get("dependencies", {})

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("section")
            .not_.is_("section", "null")
            .execute()
        )

        sections_with_data = set()
        if result.data:
            for row in result.data:
                sec = row.get("section")
                if sec:
                    sections_with_data.add(sec)
                    sections_with_data.add(f"BP.{sec}")

        blocked = []
        seen_sections = set()
        for node_id, deps in dependencies.items():
            parts = node_id.split(".")
            if len(parts) >= 2:
                section_key = f"BP.{parts[1]}"
            else:
                continue

            if section_key in seen_sections:
                continue

            missing_deps = []
            for dep in deps:
                dep_parts = dep.split(".")
                if len(dep_parts) >= 2:
                    dep_section = f"BP.{dep_parts[1]}"
                    dep_section_num = dep_parts[1]
                else:
                    continue

                if dep_section_num not in sections_with_data and dep_section not in sections_with_data:
                    if dep_section != section_key:
                        missing_deps.append(dep_section)

            if missing_deps:
                seen_sections.add(section_key)
                blocked.append({
                    "section_id": section_key,
                    "blocked_by": list(set(missing_deps)),
                })

        return blocked
    except Exception as e:
        logger.error("[CoverageCalc] Error computing blocked sections: %s", e)
        return []


def get_section_detail(section_id: str) -> dict:
    """Deep dive on one section showing all nodes, fill status, and ages.

    Args:
        section_id: Section identifier (e.g. "BP.1").

    Returns:
        Dict with section_id, nodes list (each with node_id, title,
        has_data, epistemic_status, age_days), and overall stats.
    """
    arch = _load_bp_architecture()
    all_nodes = arch.get("nodes", [])
    sections = _get_sections_from_nodes(all_nodes)

    section_nodes = sections.get(section_id, [])
    if not section_nodes:
        logger.warning(
            "[CoverageCalc] Section %s not found in architecture", section_id
        )
        return {
            "section_id": section_id,
            "nodes": [],
            "total_nodes": 0,
            "filled_nodes": 0,
            "coverage_pct": 0.0,
        }

    nodes_detail: list[dict] = []
    filled_count = 0

    try:
        from services.rag_service import retrieve
    except Exception as e:
        logger.error("[CoverageCalc] Cannot import rag_service: %s", e)
        for node in section_nodes:
            nodes_detail.append({
                "node_id": node.get("node_id", ""),
                "title": node.get("node_title", ""),
                "has_data": False,
                "epistemic_status": "UNKNOWN",
                "age_days": None,
            })
        return {
            "section_id": section_id,
            "nodes": nodes_detail,
            "total_nodes": len(section_nodes),
            "filled_nodes": 0,
            "coverage_pct": 0.0,
        }

    try:
        for node in section_nodes:
            node_id = node.get("node_id", "")
            node_title = node.get("node_title", "")
            chunks = retrieve(
                query=node_title or node_id,
                source_types=[
                    "ceo_doc", "conversation", "decision", "correction"
                ],
                section=section_id.replace("BP.", ""),
                # See matching comment in get_plan_coverage() — top_k=1 only
                # pulls 3 raw candidates before the section filter runs,
                # which starved section-specific matches out of a ~1000-fact
                # knowledge base. top_k=8 gives it a real pool to search.
                top_k=8,
                # 0.4 is this project's documented embedding calibration
                # ceiling (see matching comment in get_plan_coverage()).
                threshold=0.4,
            )

            has_data = bool(chunks)
            epistemic_status = "UNKNOWN"
            age_days: Optional[int] = None

            if chunks:
                filled_count += 1
                chunk = chunks[0]
                epistemic_status = getattr(
                    chunk, "metadata", {}
                ).get("epistemic_status", "UNKNOWN")
                created_at = getattr(chunk, "metadata", {}).get(
                    "created_at", None
                )
                if created_at:
                    try:
                        created = datetime.fromisoformat(
                            str(created_at).replace("Z", "+00:00")
                        )
                        age_days = (
                            datetime.now(timezone.utc) - created
                        ).days
                    except (ValueError, TypeError) as parse_err:
                        logger.warning(
                            "[CoverageCalc] Cannot parse date %s: %s",
                            created_at,
                            parse_err,
                        )

            nodes_detail.append({
                "node_id": node_id,
                "title": node_title,
                "has_data": has_data,
                "epistemic_status": epistemic_status,
                "age_days": age_days,
            })

        coverage_pct = round(
            (filled_count / len(section_nodes) * 100)
            if section_nodes
            else 0,
            1,
        )

        return {
            "section_id": section_id,
            "nodes": nodes_detail,
            "total_nodes": len(section_nodes),
            "filled_nodes": filled_count,
            "coverage_pct": coverage_pct,
        }
    except Exception as e:
        logger.error(
            "[CoverageCalc] Error computing section detail for %s: %s",
            section_id,
            e,
        )
        return {
            "section_id": section_id,
            "nodes": nodes_detail,
            "total_nodes": len(section_nodes),
            "filled_nodes": filled_count,
            "coverage_pct": 0.0,
        }


def get_dashboard_stats() -> dict:
    """Return all dashboard statistics in one call.

    Returns:
        Dict with coverage_pct, confidence_pct, contradiction_count, stale_count,
        oldest_assumption_age_days.
    """
    if (
        _dashboard_cache["stats"] is not None
        and _dashboard_cache["timestamp"] + _CACHE_TTL > time.time()
    ):
        return _dashboard_cache["stats"]

    try:
        confidence = get_confidence_breakdown()
        contradiction_count = get_contradiction_count()
        stale = get_stale_items()
        oldest = get_oldest_assumptions(top_k=1)

        arch = _load_bp_architecture()
        total_nodes = len(arch.get("nodes", []))

        # get_plan_coverage() does one RAG retrieve per node (746 nodes
        # today, ~15-20s cold). That cost is why this whole function is
        # wrapped in a 60s cache below — real coverage is computed at most
        # once a minute, not on every dashboard/badge fetch. Previously
        # this was hardcoded to 0.0 ("expensive; use cached value") but no
        # code path ever actually populated a real cached value, so every
        # consumer of coverage_pct (menu badges, topbar, health ring) has
        # been silently reading 0% regardless of real plan content.
        plan_coverage = get_plan_coverage()

        result = {
            "total_nodes": total_nodes,
            "coverage_pct": plan_coverage.get("coverage_pct", 0.0),
            "confidence_pct": confidence.get("confidence_pct", 0.0),
            "confirmed_count": confidence.get("confirmed_count", 0),
            "total_tagged": confidence.get("total_tagged", 0),
            "contradiction_count": contradiction_count,
            "stale_count": len(stale),
            "oldest_assumption_age_days": oldest[0]["age_days"] if oldest else 0,
        }

        _dashboard_cache["stats"] = result
        _dashboard_cache["timestamp"] = time.time()

        return result
    except Exception as e:
        logger.error("[CoverageCalc] Error computing dashboard stats: %s", e)
        return {
            "total_nodes": 0,
            "coverage_pct": 0.0,
            "confidence_pct": 0.0,
            "confirmed_count": 0,
            "total_tagged": 0,
            "contradiction_count": 0,
            "stale_count": 0,
            "oldest_assumption_age_days": 0,
        }


def invalidate_dashboard_cache() -> None:
    """Invalidate cached dashboard stats. Call after any data write."""
    _dashboard_cache["stats"] = None
    _dashboard_cache["timestamp"] = 0.0
    logger.info("[CoverageCalculator] Dashboard cache invalidated")
