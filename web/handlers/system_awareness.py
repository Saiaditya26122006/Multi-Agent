"""
System Awareness Q&A module.

Answers natural questions about the system's own state, history, and activity
(as opposed to business plan content questions). Queries live data sources and
synthesizes natural answers via Haiku.

Data source priority: Supabase (live DB) > local JSON (fallback only).
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
    r"\blates\w* (node|domain)\b",
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
    """Detect whether the user is asking about the system itself."""
    for pattern in _CONTENT_RE:
        if pattern.search(text):
            return False

    for pattern in _SYSTEM_RE:
        if pattern.search(text):
            return True

    return False


# ─── LLM-based intent classification ─────────────────────────────────────────

_INTENT_SYSTEM_PROMPT = """You are an intent classifier for EpistemicOS system questions.
Classify the user's question into EXACTLY ONE category and extract the time window.

Categories:
- architecture_changes: about nodes/domains added, modified, created, how many exist, structure
- classification_history: what was filed where, rejected, pending, auto-filed
- system_activity: processing counts, errors, failures, what happened
- data_freshness: when sections were last updated, what's stale, what's new in a section

Also extract these parameters:
- time_window: how far back the user is asking about. Use minutes as the unit.
  "today" = 1440, "yesterday" = 2880, "this week" = 10080, "last hour" = 60
  If no time specified, use 10080 (7 days).
- specific_node: if the user mentions a specific node ID (like BP.13, BP.1.2), extract it. Otherwise null.

Respond with ONLY valid JSON, no markdown:
{"intent": "<category>", "time_window_minutes": <int>, "specific_node": <string or null>}"""


def classify_system_intent_llm(text: str) -> dict:
    """Use Haiku to classify intent + extract temporal/node parameters.

    Returns dict with: intent, time_window_minutes, specific_node.
    Falls back to keyword-based classification on LLM failure.
    """
    try:
        from web.handlers.llm_helper import _get_client

        client = _get_client()
        model_id = os.getenv(
            "CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )

        response = client.converse(
            modelId=model_id,
            system=[{"text": _INTENT_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": text}]}],
            inferenceConfig={"maxTokens": 100},
        )

        raw = response["output"]["message"]["content"][0]["text"].strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        result = json.loads(raw)
        valid_intents = {
            "architecture_changes",
            "classification_history",
            "system_activity",
            "data_freshness",
        }
        if result.get("intent") not in valid_intents:
            result["intent"] = _classify_system_intent_keywords(text)

        result.setdefault("time_window_minutes", 10080)
        result.setdefault("specific_node", None)

        logger.info(
            "[SystemAwareness] LLM intent: %s, time=%dm, node=%s",
            result["intent"],
            result["time_window_minutes"],
            result.get("specific_node"),
        )
        return result

    except Exception as e:
        logger.warning(
            "[SystemAwareness] LLM intent classification failed, using keywords: %s", e
        )
        return {
            "intent": _classify_system_intent_keywords(text),
            "time_window_minutes": _extract_time_window_fallback(text),
            "specific_node": _extract_node_id_fallback(text),
        }


def _classify_system_intent_keywords(text: str) -> str:
    """Keyword-based fallback for intent classification."""
    text_lower = text.lower()

    arch_keywords = [
        "node", "domain", "created", "added", "architecture",
        "structure", "how many node", "how many domain", "exist", "latest",
    ]
    if any(kw in text_lower for kw in arch_keywords):
        return "architecture_changes"

    classify_keywords = [
        "filed", "classified", "auto-filed", "autofiled",
        "rejected", "pending", "classification",
    ]
    if any(kw in text_lower for kw in classify_keywords):
        return "classification_history"

    fresh_keywords = [
        "stale", "freshness", "updated", "new in",
        "last updated", "outdated", "old data",
    ]
    if any(kw in text_lower for kw in fresh_keywords):
        return "data_freshness"

    return "system_activity"


def _extract_time_window_fallback(text: str) -> int:
    """Extract time window from text using regex patterns."""
    text_lower = text.lower()

    if "today" in text_lower:
        return 1440
    if "yesterday" in text_lower:
        return 2880
    if "this week" in text_lower or "past week" in text_lower:
        return 10080
    if "last hour" in text_lower or "past hour" in text_lower:
        return 60
    if "last 24" in text_lower:
        return 1440

    # Match "last N hours/days/minutes"
    match = re.search(r"last (\d+)\s*(hour|day|minute|min|hr)", text_lower)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("hour") or unit.startswith("hr"):
            return num * 60
        elif unit.startswith("day"):
            return num * 1440
        elif unit.startswith("min"):
            return num

    return 10080  # Default: 7 days


def _extract_node_id_fallback(text: str) -> Optional[str]:
    """Extract a node ID (BP.X.Y.Z) from text if mentioned."""
    match = re.search(r"BP\.\d+(?:\.\d+)*", text, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    return None


# Keep the old name as a compat shim
def classify_system_intent(text: str) -> str:
    """Legacy keyword-based classifier (used by tests)."""
    return _classify_system_intent_keywords(text)


# ─── Data queries (Supabase-first) ───────────────────────────────────────────

def query_architecture_changes(
    time_window_minutes: int = 10080,
    specific_node: Optional[str] = None,
) -> dict:
    """Query Supabase (primary) + local JSON (fallback) for node/architecture data.

    Supabase is the source of truth for:
    - Which nodes have data stored
    - When nodes were last updated
    - Fact counts per node
    - Most recently active nodes

    Local JSON provides:
    - Architecture metadata (type, level, parent, purpose)
    - Candidate status

    Args:
        time_window_minutes: How far back to look for recent activity.
        specific_node: If set, focus results on this node ID.

    Returns:
        Dict with: recent_nodes, node_details, total_nodes_in_db,
        total_nodes_in_architecture.
    """
    # ── Primary: Supabase query ──
    db_data = _query_nodes_from_db(time_window_minutes, specific_node)

    # ── Secondary: local JSON for metadata enrichment ──
    arch_metadata = _get_architecture_metadata(specific_node)

    # Merge: enrich DB nodes with arch metadata
    for node in db_data.get("recent_nodes", []):
        nid = node.get("node_id", "")
        if nid in arch_metadata:
            meta = arch_metadata[nid]
            node["node_type"] = meta.get("node_type", "")
            node["level"] = meta.get("level", "")
            node["architecture_status"] = meta.get("architecture_status", "")
            node["parent_node"] = meta.get("parent_node", "")

    return {
        "recent_nodes": db_data["recent_nodes"],
        "total_nodes_in_db": db_data["total_distinct_nodes"],
        "total_facts_in_window": db_data["total_facts_in_window"],
        "total_nodes_in_architecture": arch_metadata.get("_total_count", 0),
        "time_window_minutes": time_window_minutes,
        "specific_node_query": specific_node,
    }


def _query_nodes_from_db(
    time_window_minutes: int = 10080,
    specific_node: Optional[str] = None,
) -> dict:
    """Query Supabase knowledge_base for node activity."""
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        cutoff = (
            datetime.utcnow() - timedelta(minutes=time_window_minutes)
        ).isoformat()

        # Get facts in the time window
        query = (
            supabase.table(TABLE_NAME)
            .select("content, section, metadata, created_at, source_type")
            .gte("created_at", cutoff)
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(1000)
        )

        if specific_node:
            query = query.eq("metadata->>node_id", specific_node)

        result = query.execute()
        rows = result.data or []

        # Aggregate per node
        node_map: dict = {}
        for row in rows:
            meta = row.get("metadata") or {}
            node_id = meta.get("node_id") or ""
            if not node_id:
                continue

            if node_id not in node_map:
                node_map[node_id] = {
                    "node_id": node_id,
                    "node_title": meta.get("node_title") or "",
                    "fact_count": 0,
                    "latest_fact_at": row.get("created_at") or "",
                    "earliest_fact_at": row.get("created_at") or "",
                    "sample_content": (row.get("content") or "")[:120],
                    "source_type": row.get("source_type") or "",
                }
            node_map[node_id]["fact_count"] += 1
            node_map[node_id]["earliest_fact_at"] = row.get("created_at") or ""

        # Sort by latest_fact_at descending (most recently active first)
        recent_nodes = sorted(
            node_map.values(),
            key=lambda x: x.get("latest_fact_at", ""),
            reverse=True,
        )[:30]

        # Get total distinct node count (quick estimate)
        all_query = (
            supabase.table(TABLE_NAME)
            .select("metadata")
            .not_.is_("metadata->>node_id", "null")
            .is_("superseded_by", "null")
            .limit(5000)
            .execute()
        )
        all_node_ids = set()
        for r in all_query.data or []:
            nid = (r.get("metadata") or {}).get("node_id")
            if nid:
                all_node_ids.add(nid)

        return {
            "recent_nodes": recent_nodes,
            "total_distinct_nodes": len(all_node_ids),
            "total_facts_in_window": len(rows),
        }

    except Exception as e:
        logger.error("[SystemAwareness] _query_nodes_from_db failed: %s", e)
        return {
            "recent_nodes": [],
            "total_distinct_nodes": 0,
            "total_facts_in_window": 0,
        }


def _get_architecture_metadata(
    specific_node: Optional[str] = None,
) -> dict:
    """Load architecture metadata from local JSON. Returns {node_id: metadata}."""
    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"

    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("[SystemAwareness] Could not load bp_architecture.json: %s", e)
        return {"_total_count": 0}

    nodes = data.get("nodes", [])
    real_nodes = [
        n for n in nodes if n.get("node_id") and n["node_id"].startswith("BP.")
    ]

    result: dict = {"_total_count": len(real_nodes)}

    for n in real_nodes:
        nid = n["node_id"]
        if specific_node and nid != specific_node:
            continue
        result[nid] = {
            "node_type": n.get("node_type") or "",
            "level": n.get("level") or "",
            "architecture_status": n.get("architecture_status") or "",
            "parent_node": n.get("parent_node") or "",
            "node_title": n.get("node_title") or "",
        }

    return result


def query_classification_history(
    since_minutes: int = 1440,
    specific_node: Optional[str] = None,
) -> dict:
    """Query Supabase knowledge_base for recently filed facts.

    Args:
        since_minutes: Time window in minutes (default: 24 hours).
        specific_node: If set, only show filings under this node.

    Returns:
        Dict with: total_filed, auto_filed_count, review_count,
        per_node_breakdown, new_nodes_created.
    """
    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        cutoff = (datetime.utcnow() - timedelta(minutes=since_minutes)).isoformat()

        query = (
            supabase.table(TABLE_NAME)
            .select("id, content, source_type, section, metadata, created_at")
            .gte("created_at", cutoff)
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(500)
        )

        if specific_node:
            query = query.eq("metadata->>node_id", specific_node)

        result = query.execute()
        rows = result.data or []
        total_filed = len(rows)

        per_node: dict = {}
        auto_filed_count = 0
        new_nodes_created = []

        for row in rows:
            meta = row.get("metadata") or {}
            node_id = meta.get("node_id") or row.get("section") or "unknown"
            node_title = meta.get("node_title") or ""

            if node_id not in per_node:
                per_node[node_id] = {
                    "node_id": node_id,
                    "node_title": node_title,
                    "count": 0,
                }
            per_node[node_id]["count"] += 1

            confidence = meta.get("node_confidence") or meta.get("confidence") or ""
            if confidence in ("high",):
                auto_filed_count += 1

            if confidence in ("new_node_created", "new_domain_created"):
                new_nodes_created.append({
                    "node_id": node_id,
                    "node_title": node_title,
                    "content_preview": (row.get("content") or "")[:100],
                    "created_at": row.get("created_at") or "",
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


def query_system_activity(
    since_minutes: int = 1440,
    specific_node: Optional[str] = None,
) -> dict:
    """Query Supabase events_logs for recent system activity.

    Args:
        since_minutes: Time window in minutes (default: 24 hours).
        specific_node: Unused for activity, kept for interface consistency.

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


def query_data_freshness(
    section_id: Optional[str] = None,
    specific_node: Optional[str] = None,
) -> dict:
    """Query knowledge_base for data freshness per section.

    Args:
        section_id: Optional specific section to query (legacy param).
        specific_node: If set, focus on this node's freshness.

    Returns:
        Dict with: sections (list of dicts), empty_sections, stale_sections.
    """
    target_node = specific_node or section_id

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()

        query = (
            supabase.table(TABLE_NAME)
            .select("section, metadata, created_at")
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(5000)
        )

        if target_node:
            query = query.eq("metadata->>node_id", target_node)

        result = query.execute()
        rows = result.data or []

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

        stale_threshold = (datetime.utcnow() - timedelta(days=30)).isoformat()
        stale_sections = []
        empty_sections = []

        for sec_info in section_data.values():
            last = sec_info["last_updated"]
            if last and last < stale_threshold:
                sec_info["is_stale"] = True
                stale_sections.append(sec_info["section_id"])

        # Check for top-level BP sections that have no data (from local JSON)
        arch_path = (
            Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"
        )
        try:
            with open(arch_path, "r", encoding="utf-8") as f:
                arch_data = json.load(f)
            top_sections = [
                n["node_id"]
                for n in arch_data.get("nodes", [])
                if n.get("node_id", "").startswith("BP.") and n.get("level") == 1.0
            ]
            for sec in top_sections:
                if sec not in section_data:
                    empty_sections.append(sec)
        except Exception as e:
            logger.warning(
                "[SystemAwareness] Could not load architecture for empty check: %s", e
            )

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


# ─── Main entry point ────────────────────────────────────────────────────────

def answer_system_question(text: str, session_id: Optional[str] = None) -> str:
    """Answer a system-awareness question by querying live data and synthesizing via Haiku.

    Uses LLM-based intent classification with temporal parsing for accurate routing.
    """
    from tools.trace_emitter import emit_trace

    if session_id:
        emit_trace(session_id, "System", "querying", "Looking up system state...")

    # Step 1: LLM-based intent + time window + node extraction
    classification = classify_system_intent_llm(text)
    intent = classification["intent"]
    time_window = classification["time_window_minutes"]
    specific_node = classification.get("specific_node")

    logger.info(
        "[SystemAwareness] Classified: intent=%s, window=%dm, node=%s for: %s",
        intent, time_window, specific_node, text[:80],
    )

    # Step 2: Query the appropriate data source with extracted parameters
    if intent == "architecture_changes":
        raw_data = query_architecture_changes(time_window, specific_node)
    elif intent == "classification_history":
        raw_data = query_classification_history(time_window, specific_node)
    elif intent == "data_freshness":
        raw_data = query_data_freshness(specific_node=specific_node)
    else:
        raw_data = query_system_activity(time_window, specific_node)

    if session_id:
        emit_trace(session_id, "System", "answering", "Synthesizing answer...")

    # Step 3: Synthesize via Haiku
    return _synthesize_answer(text, intent, raw_data)


def _synthesize_answer(question: str, intent: str, data: dict) -> str:
    """Call Haiku to produce a natural answer from raw query results."""
    system_prompt = (
        "You are the EpistemicOS system assistant. Answer the user's question "
        "about system state based ONLY on the data provided. Be concise (2-5 "
        "sentences). Use specific numbers, node IDs, and timestamps when available. "
        "Never invent data not in the context.\n\n"
        "IMPORTANT:\n"
        "- Data is sorted most-recent-first (by latest_fact_at or created_at).\n"
        "- When the user asks about 'latest', 'newest', or 'most recent', the FIRST "
        "entry in 'recent_nodes' is the answer.\n"
        "- Always include the node_title and created/updated timestamp.\n"
        "- If fact_count is available, mention how many facts are stored under that node."
    )

    # Serialize compactly, recent data first
    data_str = json.dumps(data, separators=(",", ":"), default=str)[:8000]

    user_message = (
        f"SYSTEM DATA ({intent}):\n{data_str}\n\n"
        f"USER QUESTION: {question}\n\n"
        "Answer based on the data above. The first entry in lists is the most recent."
    )

    try:
        from web.handlers.llm_helper import _get_client

        client = _get_client()
        model_id = os.getenv(
            "CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )

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
        total_db = data.get("total_nodes_in_db", 0)
        recent = data.get("recent_nodes", [])
        parts = [f"The system has {total_db} nodes with stored data."]
        if recent:
            top = recent[0]
            parts.append(
                f"Most recently active: {top['node_id']} \"{top.get('node_title', '')}\" "
                f"({top.get('fact_count', 0)} facts, last updated {top.get('latest_fact_at', 'unknown')})."
            )
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
