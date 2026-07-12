"""
System Awareness Q&A module.

Answers natural questions about the system's own state, history, and activity
(as opposed to business plan content questions). Queries live data sources and
synthesizes natural answers via Haiku.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Patterns for detecting system-awareness questions ────────────────────────

_SYSTEM_PATTERNS = [
    # Architecture/node changes
    r"\b(what|which)\b.*\b(nodes?|domains?)\b.*\b(added|created|new|recently)\b",
    r"\bnew (nodes?|domains?)\b",
    r"\brecently (added|created)\b",
    r"\bwhat was created\b",
    r"\bwhat (nodes?|domains?) exist\b",
    r"\barchitecture structure\b",
    r"\bhow many (nodes?|domains?)\b",
    # Classification/filing
    r"\bwhat was filed\b",
    r"\bwhat got classified\b",
    r"\b(show|list)\b.*\brecent\b.*\b(filings?|classifications?)\b",
    r"\bwhat happened\b",
    r"\bauto[- ]?filed\b",
    # Activity/stats
    r"\bhow many facts\b",
    r"\bactivity\b",
    r"\bprocessed today\b",
    r"\b(show|any|what)\b.*\b(errors?|failures?|failed)\b",
    r"\bwhat failed\b",
    r"\bsystem (stats|status|activity)\b",
    # Freshness
    r"\bwhen was .+ (last )?updated\b",
    r"\bwhat'?s stale\b",
    r"\bwhat'?s new in\b",
    r"\bfreshness\b",
    r"\blast updated\b",
    r"\bstale (data|sections?|nodes?)\b",
]

_SYSTEM_RE = [re.compile(p, re.IGNORECASE) for p in _SYSTEM_PATTERNS]

# Negative patterns — business-plan content questions that should NOT match
_CONTENT_PATTERNS = [
    r"\bwhat'?s our (TAM|SAM|market|revenue|pricing|strategy)\b",
    r"\bshow (assumptions|claims) about\b",
    r"\bwhat do we know about\b",
    r"\btell me about\b",
    r"\bwhat is .+ (product|service|market|competition)\b",
]

_CONTENT_RE = [re.compile(p, re.IGNORECASE) for p in _CONTENT_PATTERNS]


def is_system_question(text: str) -> bool:
    """Detect whether the user is asking about the system itself.

    Returns True for questions about nodes added, filing history, system
    activity, errors, freshness — but NOT for business-plan content questions.
    """
    # Check negative patterns first — if it looks like a content question, bail
    for pattern in _CONTENT_RE:
        if pattern.search(text):
            return False

    # Check positive patterns
    for pattern in _SYSTEM_RE:
        if pattern.search(text):
            return True

    return False


def classify_system_intent(text: str) -> str:
    """Classify the system question into one of four categories.

    Returns one of:
      - "architecture_changes" — about nodes/domains added/modified
      - "classification_history" — what was filed where, rejected, pending
      - "system_activity" — counts, processing stats, errors
      - "data_freshness" — when sections were updated, what's stale/new
    """
    text_lower = text.lower()

    # Architecture patterns
    arch_keywords = [
        "node", "domain", "created", "added", "architecture",
        "structure", "how many node", "how many domain", "exist",
    ]
    if any(kw in text_lower for kw in arch_keywords):
        return "architecture_changes"

    # Classification/filing patterns
    classify_keywords = [
        "filed", "classified", "auto-filed", "autofiled",
        "rejected", "pending", "classification",
    ]
    if any(kw in text_lower for kw in classify_keywords):
        return "classification_history"

    # Freshness patterns
    fresh_keywords = [
        "stale", "freshness", "updated", "new in",
        "last updated", "outdated", "old data",
    ]
    if any(kw in text_lower for kw in fresh_keywords):
        return "data_freshness"

    # Default to system activity (errors, counts, general "what happened")
    return "system_activity"


def query_architecture_changes() -> dict:
    """Query bp_architecture.json for recently created/candidate nodes.

    Returns:
        Dict with: candidate_nodes (list), auto_created_nodes (list),
        total_nodes (int), total_candidates (int).
    """
    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"

    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("[SystemAwareness] Failed to read bp_architecture.json: %s", e)
        return {
            "candidate_nodes": [],
            "auto_created_nodes": [],
            "total_nodes": 0,
            "total_candidates": 0,
        }

    nodes = data.get("nodes", [])
    # Skip the header/schema row (first entry has no real node_id like "BP.X")
    real_nodes = [
        n for n in nodes
        if n.get("node_id") and n["node_id"].startswith("BP.")
    ]

    candidate_nodes = []
    auto_created_nodes = []

    for n in real_nodes:
        if n.get("architecture_status") == "candidate":
            candidate_nodes.append({
                "node_id": n["node_id"],
                "node_title": n.get("node_title") or "",
                "parent_node": n.get("parent_node") or "",
                "notes_limitations": (n.get("notes_limitations") or "")[:200],
            })

        notes = n.get("notes_limitations") or ""
        if "Created automatically via Feed" in notes:
            auto_created_nodes.append({
                "node_id": n["node_id"],
                "node_title": n.get("node_title") or "",
                "created_context": notes[:200],
                "parent_node": n.get("parent_node") or "",
            })

    return {
        "candidate_nodes": candidate_nodes,
        "auto_created_nodes": auto_created_nodes,
        "total_nodes": len(real_nodes),
        "total_candidates": len(candidate_nodes),
    }


def query_classification_history(since_minutes: int = 1440) -> dict:
    """Query Supabase knowledge_base for recently filed facts.

    Args:
        since_minutes: Time window in minutes (default: 24 hours).

    Returns:
        Dict with: total_filed, auto_filed_count, review_count,
        per_node_breakdown, new_nodes_created.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        cutoff = (datetime.utcnow() - timedelta(minutes=since_minutes)).isoformat()

        # Get all facts filed in the window
        result = (
            supabase.table(TABLE_NAME)
            .select("id, content, source_type, section, metadata, created_at")
            .gte("created_at", cutoff)
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )

        rows = result.data or []
        total_filed = len(rows)

        # Breakdown by node
        per_node: dict = {}
        auto_filed_count = 0
        new_nodes_created = []

        for row in rows:
            meta = row.get("metadata") or {}
            node_id = meta.get("node_id") or row.get("section") or "unknown"
            node_title = meta.get("node_title") or ""

            if node_id not in per_node:
                per_node[node_id] = {"node_id": node_id, "node_title": node_title, "count": 0}
            per_node[node_id]["count"] += 1

            # Detect auto-filed vs review
            confidence = meta.get("node_confidence") or meta.get("confidence") or ""
            if confidence in ("high",):
                auto_filed_count += 1

            # Detect new node creations
            if confidence in ("new_node_created", "new_domain_created"):
                new_nodes_created.append({
                    "node_id": node_id,
                    "node_title": node_title,
                    "content_preview": (row.get("content") or "")[:100],
                })

        review_count = total_filed - auto_filed_count

        return {
            "total_filed": total_filed,
            "auto_filed_count": auto_filed_count,
            "review_count": review_count,
            "per_node_breakdown": sorted(
                per_node.values(), key=lambda x: x["count"], reverse=True
            )[:20],
            "new_nodes_created": new_nodes_created,
            "since_minutes": since_minutes,
        }
    except Exception as e:
        logger.error("[SystemAwareness] query_classification_history failed: %s", e)
        return {
            "total_filed": 0,
            "auto_filed_count": 0,
            "review_count": 0,
            "per_node_breakdown": [],
            "new_nodes_created": [],
            "since_minutes": since_minutes,
        }


def query_system_activity(since_minutes: int = 1440) -> dict:
    """Query Supabase events_logs for recent system activity.

    Args:
        since_minutes: Time window in minutes (default: 24 hours).

    Returns:
        Dict with: total_events, classifications, errors, auto_files,
        manual_reviews.
    """
    try:
        from services.rag_service import _get_supabase

        supabase = _get_supabase()
        cutoff = (datetime.utcnow() - timedelta(minutes=since_minutes)).isoformat()

        result = (
            supabase.table("events_logs")
            .select("id, agent_id, action, created_at")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1000)
            .execute()
        )

        rows = result.data or []
        total_events = len(rows)

        classifications = 0
        errors = 0
        auto_files = 0
        manual_reviews = 0

        for row in rows:
            action = (row.get("action") or "").lower()
            if "classif" in action:
                classifications += 1
            if "error" in action or "fail" in action:
                errors += 1
            if "auto_file" in action or "auto_store" in action:
                auto_files += 1
            if "review" in action or "manual" in action:
                manual_reviews += 1

        # Get recent errors with more detail
        error_result = (
            supabase.table("events_logs")
            .select("id, agent_id, action, input_ref, created_at")
            .gte("created_at", cutoff)
            .or_("action.ilike.%error%,action.ilike.%fail%")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

        recent_errors = [
            {
                "agent": row.get("agent_id") or "",
                "action": row.get("action") or "",
                "detail": (row.get("input_ref") or "")[:100],
                "time": row.get("created_at") or "",
            }
            for row in (error_result.data or [])
        ]

        return {
            "total_events": total_events,
            "classifications": classifications,
            "errors": errors,
            "auto_files": auto_files,
            "manual_reviews": manual_reviews,
            "recent_errors": recent_errors,
            "since_minutes": since_minutes,
        }
    except Exception as e:
        logger.error("[SystemAwareness] query_system_activity failed: %s", e)
        return {
            "total_events": 0,
            "classifications": 0,
            "errors": 0,
            "auto_files": 0,
            "manual_reviews": 0,
            "recent_errors": [],
            "since_minutes": since_minutes,
        }


def query_data_freshness(section_id: Optional[str] = None) -> dict:
    """Query knowledge_base for data freshness per section.

    Args:
        section_id: Optional specific section to query. If None, returns all.

    Returns:
        Dict with: sections (list of dicts), empty_sections, stale_sections.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()

        # Get the most recent created_at per section
        query = (
            supabase.table(TABLE_NAME)
            .select("section, metadata, created_at")
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(5000)
        )

        if section_id:
            query = query.eq("section", section_id)

        result = query.execute()
        rows = result.data or []

        # Group by section
        section_data: dict = {}
        for row in rows:
            meta = row.get("metadata") or {}
            sec = meta.get("node_id") or row.get("section") or "unknown"
            node_title = meta.get("node_title") or ""

            if sec not in section_data:
                section_data[sec] = {
                    "section_id": sec,
                    "title": node_title,
                    "last_updated": row.get("created_at"),
                    "fact_count": 0,
                    "is_stale": False,
                }
            section_data[sec]["fact_count"] += 1

        # Mark stale (>30 days since last update)
        stale_threshold = (datetime.utcnow() - timedelta(days=30)).isoformat()
        stale_sections = []
        empty_sections = []

        for sec_info in section_data.values():
            last = sec_info["last_updated"]
            if last and last < stale_threshold:
                sec_info["is_stale"] = True
                stale_sections.append(sec_info["section_id"])

        # Check for top-level BP sections that have no data
        arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"
        try:
            with open(arch_path, "r", encoding="utf-8") as f:
                arch_data = json.load(f)
            top_sections = [
                n["node_id"] for n in arch_data.get("nodes", [])
                if n.get("node_id", "").startswith("BP.") and n.get("level") == 1.0
            ]
            for sec in top_sections:
                if sec not in section_data:
                    empty_sections.append(sec)
        except Exception as e:
            logger.warning("[SystemAwareness] Could not load architecture for empty section check: %s", e)

        sections_list = sorted(
            section_data.values(),
            key=lambda x: x["last_updated"] or "",
            reverse=True,
        )[:50]

        return {
            "sections": sections_list,
            "empty_sections": empty_sections,
            "stale_sections": stale_sections,
            "total_sections_with_data": len(section_data),
        }
    except Exception as e:
        logger.error("[SystemAwareness] query_data_freshness failed: %s", e)
        return {
            "sections": [],
            "empty_sections": [],
            "stale_sections": [],
            "total_sections_with_data": 0,
        }


def answer_system_question(text: str, session_id: Optional[str] = None) -> str:
    """Answer a system-awareness question by querying live data and synthesizing via Haiku.

    Args:
        text: The user's question.
        session_id: Optional session ID for trace emission.

    Returns:
        Natural-language answer string.
    """
    from tools.trace_emitter import emit_trace

    if session_id:
        emit_trace(session_id, "System", "querying", "Looking up system state...")

    intent = classify_system_intent(text)
    logger.info("[SystemAwareness] Intent classified as '%s' for: %s", intent, text[:80])

    # Query the appropriate data source
    if intent == "architecture_changes":
        raw_data = query_architecture_changes()
    elif intent == "classification_history":
        raw_data = query_classification_history()
    elif intent == "data_freshness":
        raw_data = query_data_freshness()
    else:
        raw_data = query_system_activity()

    if session_id:
        emit_trace(session_id, "System", "answering", "Synthesizing answer...")

    # Synthesize via Haiku
    return _synthesize_answer(text, intent, raw_data)


def _synthesize_answer(question: str, intent: str, data: dict) -> str:
    """Call Haiku to produce a natural answer from raw query results.

    Falls back to a formatted summary if the LLM call fails.
    """
    system_prompt = (
        "You are the EpistemicOS system assistant. Answer the user's question "
        "about system state based ONLY on the data provided. Be concise (2-5 "
        "sentences). Use specific numbers and node IDs when available. Never "
        "invent data not in the context."
    )

    # Serialize data to a compact representation for the prompt
    data_str = json.dumps(data, indent=2, default=str)[:3000]

    user_message = (
        f"SYSTEM DATA ({intent}):\n{data_str}\n\n"
        f"USER QUESTION: {question}\n\n"
        "Answer concisely based on the data above."
    )

    try:
        from web.handlers.llm_helper import _get_client

        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 512},
        )

        answer = response["output"]["message"]["content"][0]["text"]
        logger.info(
            "[SystemAwareness] Synthesized answer (%d tokens in, %d out)",
            response.get("usage", {}).get("inputTokens", 0),
            response.get("usage", {}).get("outputTokens", 0),
        )
        return answer

    except Exception as e:
        logger.error("[SystemAwareness] Haiku synthesis failed, using fallback: %s", e)
        return _fallback_answer(intent, data)


def _fallback_answer(intent: str, data: dict) -> str:
    """Produce a readable fallback answer when the LLM call fails."""
    if intent == "architecture_changes":
        total = data.get("total_nodes", 0)
        candidates = data.get("total_candidates", 0)
        auto = len(data.get("auto_created_nodes", []))
        parts = [f"The architecture has {total} nodes total."]
        if candidates:
            parts.append(f"{candidates} are candidates (newly created).")
        if auto:
            parts.append(f"{auto} were auto-created via Feed.")
        return " ".join(parts)

    elif intent == "classification_history":
        total = data.get("total_filed", 0)
        auto = data.get("auto_filed_count", 0)
        new_nodes = len(data.get("new_nodes_created", []))
        mins = data.get("since_minutes", 1440)
        hours = mins // 60
        parts = [f"In the last {hours}h: {total} facts filed."]
        if auto:
            parts.append(f"{auto} auto-filed (high confidence).")
        if new_nodes:
            parts.append(f"{new_nodes} new node(s) created.")
        return " ".join(parts)

    elif intent == "system_activity":
        total = data.get("total_events", 0)
        errors = data.get("errors", 0)
        classifications = data.get("classifications", 0)
        parts = [f"System activity: {total} events logged."]
        if classifications:
            parts.append(f"{classifications} classifications.")
        if errors:
            parts.append(f"{errors} error(s) detected.")
        else:
            parts.append("No errors.")
        return " ".join(parts)

    elif intent == "data_freshness":
        total_sec = data.get("total_sections_with_data", 0)
        stale = len(data.get("stale_sections", []))
        empty = len(data.get("empty_sections", []))
        parts = [f"{total_sec} sections have data."]
        if stale:
            parts.append(f"{stale} section(s) are stale (>30 days).")
        if empty:
            parts.append(f"{empty} top-level section(s) have no data yet.")
        return " ".join(parts)

    return "System data retrieved but synthesis unavailable."
