"""
FEED Workspace Handler — processes raw data input from Alex.

Handles: raw text, corrections, targeted additions, structured data.
Detects format, splits into atomic facts, classifies content type,
matches to BP architecture nodes, presents for approval, and stores.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

PENDING_FACT_TTL = 600  # 10 minutes

CONTENT_TYPE_PATTERNS = {
    "decision": [
        (r"(we decided|decision:|i decided|the decision is|we chose|we will go with)", 0.9),
        (r"(approved|rejected|killed|chose .+ over)", 0.85),
        (r"(going with|not going with|final call)", 0.8),
    ],
    "risk": [
        (r"(risk:|the risk is|biggest risk|main risk|key risk)", 0.9),
        (r"(could fail|might not work|danger|threat|vulnerability)", 0.8),
        (r"(worst case|if .+ fails|downside)", 0.75),
    ],
    "metric": [
        (r"(\d+%|\$[\d,.]+|[\d,.]+ users|[\d,.]+ customers)", 0.85),
        (r"(revenue|arpu|cac|ltv|churn|conversion|retention|mrr|arr)", 0.8),
        (r"(target:|goal:|kpi:|metric:)", 0.9),
    ],
    "constraint": [
        (r"(must not|cannot|we can't|we don't have|budget is|deadline)", 0.85),
        (r"(limitation|constraint|restriction|blocker|dependency)", 0.8),
        (r"(not allowed|prohibited|out of scope)", 0.8),
    ],
    "task": [
        (r"(need to|todo|action item|next step|we should)", 0.75),
        (r"(task:|action:|deliverable:)", 0.9),
        (r"(by (monday|tuesday|wednesday|thursday|friday|next week|end of))", 0.8),
    ],
    "open_question": [
        (r"(still unclear|don't know yet|need to find out|unresolved|tbd)", 0.85),
        (r"(question:|open question:|unsure whether)", 0.9),
        (r"(haven't decided|not sure if|need more data on)", 0.8),
    ],
    "assumption": [
        (r"(i assume|we assume|assumption:|assuming that|hypothesis)", 0.9),
        (r"(i think|i believe|probably|likely|should be|expected to)", 0.75),
        (r"(untested|unvalidated|guess|bet is that)", 0.8),
    ],
    "fact": [
        (r"(confirmed|verified|proven|we know|evidence shows|data shows)", 0.85),
        (r"(the contract states|according to|research shows|study found)", 0.85),
    ],
}

EPISTEMIC_STATUS_BY_CONTENT_TYPE = {
    "decision": "CONFIRMED",
    "risk": "INFERRED",
    "metric": "CONFIRMED",
    "constraint": "CONFIRMED",
    "task": "INFERRED",
    "open_question": "MISSING",
    "assumption": "ASSUMPTION",
    "fact": "CONFIRMED",
}


def classify_content_type(text: str) -> dict:
    """Classify the semantic content type of a fact.

    Args:
        text: A single extracted fact/claim.

    Returns:
        Dict with: content_type, confidence, matched_pattern.
    """
    text_lower = text.lower()
    best_type = "fact"
    best_confidence = 0.0
    best_pattern = None

    for content_type, patterns in CONTENT_TYPE_PATTERNS.items():
        for pattern, conf in patterns:
            if re.search(pattern, text_lower):
                if conf > best_confidence:
                    best_type = content_type
                    best_confidence = conf
                    best_pattern = pattern

    if best_confidence == 0.0:
        best_type = "fact"
        best_confidence = 0.5
        best_pattern = None

    return {
        "content_type": best_type,
        "confidence": best_confidence,
        "matched_pattern": best_pattern,
        "low_confidence": best_confidence < 0.7,
    }


def _load_bp_architecture() -> list[dict]:
    """Load BP architecture nodes from the JSON file."""
    from pathlib import Path

    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"
    if not arch_path.exists():
        logger.warning("[FeedHandler] bp_architecture.json not found at %s", arch_path)
        return []

    with open(arch_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("nodes", [])


def _get_node_details(node_id: str) -> Optional[dict]:
    """Look up a node's full details from the architecture file."""
    nodes = _load_bp_architecture()
    for node in nodes:
        if node.get("node_id") == node_id:
            return node
    return None


def _get_suggested_parent(candidates: list[dict]) -> dict:
    """Determine the best parent node for a potential new node."""
    if candidates:
        top = candidates[0]
        node_details = _get_node_details(top["node_id"])
        if node_details:
            parent_id = node_details.get("parent_node") or top["node_id"]
            parent_details = _get_node_details(parent_id)
            if parent_details:
                return {
                    "node_id": parent_id,
                    "node_title": parent_details.get("node_title", parent_id),
                }
            return {"node_id": parent_id, "node_title": parent_id}
    nodes = _load_bp_architecture()
    level_1 = [n for n in nodes if n.get("level") == 1]
    if level_1:
        return {"node_id": level_1[0]["node_id"], "node_title": level_1[0].get("node_title", "")}
    return {"node_id": "BP.1", "node_title": "Product, Workflow, and Scope Definition"}


def match_bp_node(text: str, top_k: int = 3) -> list[dict]:
    """Find the best-matching BP architecture node(s) for a fact.

    Uses semantic similarity against node titles and purposes.
    Falls back to direct embedding comparison against the architecture JSON.

    Args:
        text: The fact text to match.
        top_k: Number of candidates to return.

    Returns:
        List of dicts with: node_id, node_title, similarity, level, purpose, parent_node.
    """
    try:
        from services.rag_service import retrieve

        chunks = retrieve(
            query=text,
            source_types=["ceo_doc"],
            top_k=top_k,
            threshold=0.35,
            metadata_filter={"layer": "bp_architecture"},
        )

        if chunks:
            results = []
            for chunk in chunks[:top_k]:
                node_id = chunk.metadata.get("node_id", chunk.section or "unknown")
                node_title = chunk.metadata.get("node_title", "")
                if not node_title and chunk.content:
                    node_title = chunk.content[:60]
                node_details = _get_node_details(node_id)
                results.append({
                    "node_id": node_id,
                    "node_title": node_title,
                    "similarity": round(chunk.similarity, 3),
                    "level": chunk.metadata.get("level", 0),
                    "purpose": node_details.get("purpose", "") if node_details else "",
                    "parent_node": node_details.get("parent_node", "") if node_details else "",
                })
            return results
    except Exception as e:
        logger.debug("[FeedHandler] RAG node retrieval failed, using direct match: %s", e)

    return _direct_node_match(text, top_k)


_node_embedding_cache: Optional[list[dict]] = None


def _get_node_embeddings() -> list[dict]:
    """Return cached node embeddings, computing them on first call."""
    global _node_embedding_cache
    if _node_embedding_cache is not None:
        return _node_embedding_cache

    from services.rag_service import embed

    nodes = _load_bp_architecture()
    if not nodes:
        return []

    import numpy as np

    cache = []
    for node in nodes:
        match_text = f"{node.get('node_title', '')}. {node.get('purpose', '')}. {node.get('required_output', '')}"
        embedding = np.array(embed(match_text))
        cache.append({
            "node_id": node.get("node_id", "unknown"),
            "node_title": node.get("node_title", ""),
            "level": node.get("level", 0),
            "purpose": node.get("purpose", ""),
            "parent_node": node.get("parent_node", ""),
            "embedding": embedding,
        })

    _node_embedding_cache = cache
    logger.info("[FeedHandler] Cached embeddings for %d architecture nodes", len(cache))
    return _node_embedding_cache


def _direct_node_match(text: str, top_k: int = 3) -> list[dict]:
    """Match text against architecture nodes using cached embedding comparison."""
    try:
        from services.rag_service import embed
    except Exception as e:
        logger.error("[FeedHandler] Cannot load embedding model: %s", e)
        return []

    node_embeddings = _get_node_embeddings()
    if not node_embeddings:
        return []

    import numpy as np
    query_vec = np.array(embed(text))

    scored = []
    for node in node_embeddings:
        similarity = float(np.dot(query_vec, node["embedding"]))
        scored.append({
            "node_id": node["node_id"],
            "node_title": node["node_title"],
            "similarity": round(similarity, 3),
            "level": node["level"],
            "purpose": node.get("purpose", ""),
            "parent_node": node.get("parent_node", ""),
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def _get_redis():
    """Get the Redis client."""
    from memory.redis_client import redis_client
    return redis_client


def _pending_key(session_id: str) -> str:
    """Redis key for pending fact awaiting approval."""
    return f"feed_pending:{session_id}"


def _state_key(session_id: str) -> str:
    """Redis key for feed handler state."""
    return f"feed_state:{session_id}"


def get_feed_state(session_id: str) -> Optional[str]:
    """Get the current feed handler state for a session.

    Returns:
        State string or None if no special state is active.
    """
    try:
        r = _get_redis()
        value = r.get(_state_key(session_id))
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return value
    except Exception as e:
        logger.error("[FeedHandler] Redis error getting feed state: %s", e)
        return None


def set_feed_state(session_id: str, state: Optional[str]) -> None:
    """Set the feed handler state for a session.

    Args:
        session_id: Session identifier.
        state: State string or None to clear.
    """
    try:
        r = _get_redis()
        if state is None:
            r.delete(_state_key(session_id))
        else:
            r.set(_state_key(session_id), state, ex=PENDING_FACT_TTL)
    except Exception as e:
        logger.error("[FeedHandler] Redis error setting feed state: %s", e)


def store_pending_fact(session_id: str, fact_data: dict) -> None:
    """Store a pending fact in Redis awaiting Alex's approval.

    Args:
        session_id: Session identifier.
        fact_data: Full fact dict (text, content_type, epistemic_status, node, etc).
    """
    try:
        r = _get_redis()
        r.set(_pending_key(session_id), json.dumps(fact_data), ex=PENDING_FACT_TTL)
    except Exception as e:
        logger.error("[FeedHandler] Redis error storing pending fact: %s", e)


def get_pending_fact(session_id: str) -> Optional[dict]:
    """Retrieve the pending fact from Redis.

    Returns:
        The pending fact dict or None.
    """
    try:
        r = _get_redis()
        raw = r.get(_pending_key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        logger.error("[FeedHandler] Redis error getting pending fact: %s", e)
        return None


def clear_pending_fact(session_id: str) -> None:
    """Remove the pending fact and clear approval state."""
    try:
        r = _get_redis()
        r.delete(_pending_key(session_id))
    except Exception as e:
        logger.error("[FeedHandler] Redis error clearing pending fact: %s", e)


def _handle_duplicate_confirm(response_text: str, session_id: str) -> dict:
    """Handle Alex's response to the duplicate detection prompt.

    Args:
        response_text: 'yes' to proceed with storage, 'skip' to discard.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    text_lower = response_text.strip().lower()

    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "expired",
            "response_text": "Duplicate confirmation expired. Please re-submit.",
        }

    original_text = pending.get("original_text", "")
    if not original_text:
        clear_pending_fact(session_id)
        set_feed_state(session_id, None)
        return {
            "action": "expired",
            "response_text": "Original text lost. Please re-submit.",
        }

    if text_lower in ("yes", "y", "ok", "store", "1"):
        clear_pending_fact(session_id)
        set_feed_state(session_id, None)
        return handle_raw_text(original_text, session_id=session_id, _skip_dupe_check=True)

    clear_pending_fact(session_id)
    set_feed_state(session_id, None)
    return {
        "action": "skipped",
        "response_text": "Duplicate skipped. Nothing was stored.",
    }


def handle_raw_text(
    text: str,
    session_id: Optional[str] = None,
    _skip_dupe_check: bool = False,
) -> dict:
    """Process raw text: detect format, split, classify, match, present for approval.

    Processes ONE fact at a time. If multiple facts are extracted,
    queues the first for approval and notes how many remain.

    Args:
        text: Raw text from Alex.
        session_id: Current session ID.
        _skip_dupe_check: Internal flag to bypass duplicate detection on re-entry.

    Returns:
        Dict with: action, fact_data, review_text, remaining_count.
    """
    if not _skip_dupe_check:
        try:
            from services.rag_service import retrieve as rag_retrieve

            dupes = rag_retrieve(query=text, top_k=1, threshold=0.95)
            if dupes and dupes[0].similarity > 0.95:
                if session_id:
                    store_pending_fact(session_id, {"original_text": text})
                    set_feed_state(session_id, "FEED_AWAITING_DUPLICATE_CONFIRM")
                return {
                    "action": "duplicate_detected",
                    "response_text": (
                        "This looks like something already in the knowledge base. "
                        "Want to store it again anyway? [yes/skip]"
                    ),
                }
        except Exception as e:
            logger.debug("[FeedHandler] Duplicate check failed (non-fatal): %s", e)

    try:
        fmt = detect_format(text)
        facts = split_into_atomic_facts(text, fmt)

        if not facts:
            return {
                "action": "no_facts",
                "response_text": "No extractable facts found in that input. Try rephrasing or adding more detail.",
            }

        first_fact = facts[0]
        fact_text = first_fact["text"]

        content_classification = classify_content_type(fact_text)
        content_type = content_classification["content_type"]

        epistemic_status = EPISTEMIC_STATUS_BY_CONTENT_TYPE.get(
            content_type, first_fact.get("inferred_status", "INFERRED")
        )

        node_matches = match_bp_node(fact_text)

        proposed_node = None
        node_confidence = "unmatched"
        if node_matches and node_matches[0]["similarity"] >= 0.6:
            proposed_node = node_matches[0]
            node_confidence = "matched"
        elif node_matches:
            proposed_node = node_matches[0]
            node_confidence = "low_confidence"

        fact_data = {
            "verbatim_text": fact_text,
            "content_type": content_type,
            "content_confidence": content_classification["confidence"],
            "epistemic_status": epistemic_status,
            "proposed_node": proposed_node,
            "node_confidence": node_confidence,
            "all_node_candidates": node_matches[:3],
            "source_format": fmt,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "remaining_facts": [f["text"] for f in facts[1:]],
        }

        if session_id:
            store_pending_fact(session_id, fact_data)
            set_feed_state(session_id, "FEED_AWAITING_APPROVAL")

        review_text = _format_review_block(fact_data, remaining=len(facts) - 1)

        return {
            "action": "awaiting_approval",
            "fact_data": fact_data,
            "response_text": review_text,
            "remaining_count": len(facts) - 1,
        }
    except Exception as e:
        logger.error("[FeedHandler] Error in handle_raw_text: %s", e)
        return {
            "action": "error",
            "response_text": f"Error processing input: {e}",
        }


def handle_approval_response(
    response_text: str, session_id: str
) -> dict:
    """Handle Alex's response to a pending fact review.

    Routing depends on the node_confidence level:
    - "matched": [1]=approve, [2]=adjust, [3]=create new
    - "low_confidence": [1-3]=pick candidate, [4]=create under parent, [5]=pick parent
    - "unmatched" (very low): [1]=create under parent, [2]=pick parent

    Args:
        response_text: Alex's reply.
        session_id: Current session ID.

    Returns:
        Dict with: action, response_text, and any follow-up state.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact to approve. The review may have expired. Please re-submit your data.",
        }

    text_lower = response_text.strip().lower()
    node_confidence = pending.get("node_confidence", "unmatched")
    candidates = pending.get("all_node_candidates", [])
    all_below_threshold = not candidates or candidates[0].get("similarity", 0) < 0.3

    if text_lower in ("skip", "no", "drop", "cancel"):
        clear_pending_fact(session_id)
        remaining = pending.get("remaining_facts", [])
        if remaining:
            set_feed_state(session_id, None)
            return _process_next_fact(remaining, session_id)
        set_feed_state(session_id, None)
        return {
            "action": "skipped",
            "response_text": "Fact skipped. Nothing was stored.",
        }

    if node_confidence == "matched":
        return _handle_matched_response(text_lower, pending, session_id)

    if all_below_threshold:
        return _handle_very_low_response(text_lower, pending, session_id)

    return _handle_low_confidence_response(text_lower, pending, session_id)


def _handle_matched_response(text_lower: str, pending: dict, session_id: str) -> dict:
    """Handle response when node match is confident (>=0.6)."""
    if text_lower in ("approve", "yes", "y", "ok", "confirmed", "1"):
        return _execute_approval(pending, session_id)

    if text_lower in ("adjust", "change node", "different node", "2"):
        set_feed_state(session_id, "FEED_AWAITING_NODE_SELECTION")
        candidates = pending.get("all_node_candidates", [])
        lines = ["Which node should this go to?\n"]
        for i, candidate in enumerate(candidates, 1):
            lines.append(
                f"  [{i}] {candidate['node_id']} — {candidate['node_title']} "
                f"({int(candidate['similarity'] * 100)}% match)"
            )
        lines.append("\n  Or type a node ID directly (e.g. BP.1.1.3).")
        return {
            "action": "awaiting_node_selection",
            "response_text": "\n".join(lines),
        }

    if text_lower in ("create", "new node", "create new node", "3"):
        suggested_parent = pending.get("suggested_parent") or _get_suggested_parent(
            pending.get("all_node_candidates", [])
        )
        set_feed_state(session_id, "FEED_AWAITING_NEW_NODE_NAME")
        pending["suggested_parent"] = suggested_parent
        store_pending_fact(session_id, pending)
        return {
            "action": "awaiting_new_node_name",
            "response_text": (
                f"Creating a new node under {suggested_parent['node_id']} — {suggested_parent['node_title']}.\n"
                "What should it be called?"
            ),
        }

    return {
        "action": "unrecognized",
        "response_text": (
            "Reply with:\n"
            "  [1] Approve — store as shown\n"
            "  [2] Adjust — pick a different node\n"
            "  [3] Create new node — flag for review\n"
            "  [skip] — discard this fact"
        ),
    }


def _handle_low_confidence_response(text_lower: str, pending: dict, session_id: str) -> dict:
    """Handle response when candidates exist but none above 0.6."""
    candidates = pending.get("all_node_candidates", [])
    suggested_parent = pending.get("suggested_parent") or _get_suggested_parent(candidates)

    if text_lower == "1" and len(candidates) >= 1:
        pending["proposed_node"] = candidates[0]
        pending["node_confidence"] = "manual_override"
        store_pending_fact(session_id, pending)
        return _execute_approval(pending, session_id)

    if text_lower == "2" and len(candidates) >= 2:
        pending["proposed_node"] = candidates[1]
        pending["node_confidence"] = "manual_override"
        store_pending_fact(session_id, pending)
        return _execute_approval(pending, session_id)

    if text_lower == "3" and len(candidates) >= 3:
        pending["proposed_node"] = candidates[2]
        pending["node_confidence"] = "manual_override"
        store_pending_fact(session_id, pending)
        return _execute_approval(pending, session_id)

    if text_lower == "4":
        set_feed_state(session_id, "FEED_AWAITING_NEW_NODE_NAME")
        pending["suggested_parent"] = suggested_parent
        store_pending_fact(session_id, pending)
        return {
            "action": "awaiting_new_node_name",
            "response_text": (
                f"Creating a new node under {suggested_parent['node_id']} — {suggested_parent['node_title']}.\n"
                "What should it be called?"
            ),
        }

    if text_lower == "5":
        set_feed_state(session_id, "FEED_AWAITING_PARENT_SELECTION")
        return {
            "action": "awaiting_parent_selection",
            "response_text": (
                "Which node should be the parent?\n"
                "Reply with a node ID (e.g. BP.2.1) or a title."
            ),
        }

    return {
        "action": "unrecognized",
        "response_text": (
            "Reply with a number:\n"
            f"  [1] Use {candidates[0]['node_title'] if candidates else '?'}\n"
            + (f"  [2] Use {candidates[1]['node_title']}\n" if len(candidates) > 1 else "")
            + (f"  [3] Use {candidates[2]['node_title']}\n" if len(candidates) > 2 else "")
            + f"  [4] Create new node under {suggested_parent['node_id']}\n"
            "  [5] Pick a different parent\n"
            "  [skip] — discard"
        ),
    }


def _handle_very_low_response(text_lower: str, pending: dict, session_id: str) -> dict:
    """Handle response when all candidates are below 0.3 (no useful matches)."""
    candidates = pending.get("all_node_candidates", [])
    suggested_parent = pending.get("suggested_parent") or _get_suggested_parent(candidates)

    if text_lower == "1":
        set_feed_state(session_id, "FEED_AWAITING_NEW_NODE_NAME")
        pending["suggested_parent"] = suggested_parent
        store_pending_fact(session_id, pending)
        return {
            "action": "awaiting_new_node_name",
            "response_text": (
                f"Creating a new node under {suggested_parent['node_id']} — {suggested_parent['node_title']}.\n"
                "What should it be called?"
            ),
        }

    if text_lower == "2":
        set_feed_state(session_id, "FEED_AWAITING_PARENT_SELECTION")
        return {
            "action": "awaiting_parent_selection",
            "response_text": (
                "Which node should be the parent?\n"
                "Reply with a node ID (e.g. BP.2.1) or a title."
            ),
        }

    return {
        "action": "unrecognized",
        "response_text": (
            "Reply with:\n"
            f"  [1] Create new node under {suggested_parent['node_id']}\n"
            "  [2] Pick a different parent\n"
            "  [skip] — discard"
        ),
    }


def handle_node_selection(response_text: str, session_id: str) -> dict:
    """Handle Alex's node selection after choosing 'adjust'.

    Args:
        response_text: Node number, node ID, or cancel.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    text = response_text.strip()
    candidates = pending.get("all_node_candidates", [])

    selected_node = None

    if text.isdigit() and 1 <= int(text) <= len(candidates):
        selected_node = candidates[int(text) - 1]
    elif re.match(r"^BP\.\d", text, re.IGNORECASE):
        nodes = _load_bp_architecture()
        for node in nodes:
            if node.get("node_id", "").upper() == text.upper():
                selected_node = {
                    "node_id": node["node_id"],
                    "node_title": node.get("node_title", ""),
                    "similarity": 1.0,
                    "level": node.get("level", 0),
                }
                break

    if selected_node is None:
        return {
            "action": "invalid_selection",
            "response_text": f"Couldn't find node '{text}'. Type a number (1-{len(candidates)}) or a valid BP node ID.",
        }

    pending["proposed_node"] = selected_node
    pending["node_confidence"] = "manual_override"
    store_pending_fact(session_id, pending)

    return _execute_approval(pending, session_id)


def handle_parent_selection(response_text: str, session_id: str) -> dict:
    """Handle Alex's parent node selection (option 5 in low-confidence flow).

    Args:
        response_text: Node ID or title from Alex.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    text = response_text.strip()
    nodes = _load_bp_architecture()
    selected_parent = None

    for node in nodes:
        if node.get("node_id", "").upper() == text.upper():
            selected_parent = {"node_id": node["node_id"], "node_title": node.get("node_title", "")}
            break
        if node.get("node_title", "").lower() == text.lower():
            selected_parent = {"node_id": node["node_id"], "node_title": node["node_title"]}
            break

    if not selected_parent:
        for node in nodes:
            if text.lower() in node.get("node_title", "").lower():
                selected_parent = {"node_id": node["node_id"], "node_title": node["node_title"]}
                break

    if not selected_parent:
        return {
            "action": "invalid_parent",
            "response_text": f"Couldn't find node '{text}'. Try a node ID (e.g. BP.2.1) or an exact title.",
        }

    pending["suggested_parent"] = selected_parent
    store_pending_fact(session_id, pending)
    set_feed_state(session_id, "FEED_AWAITING_NEW_NODE_NAME")

    return {
        "action": "awaiting_new_node_name",
        "response_text": (
            f"Creating a new node under {selected_parent['node_id']} — {selected_parent['node_title']}.\n"
            "What should it be called?"
        ),
    }


def handle_new_node_name(response_text: str, session_id: str) -> dict:
    """Handle Alex's new node name after choosing 'create new node'.

    Args:
        response_text: The proposed node name.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    node_name = response_text.strip()
    if len(node_name) < 3:
        return {
            "action": "invalid_name",
            "response_text": "Node name too short. Please provide a descriptive title (3+ characters).",
        }

    suggested_parent = pending.get("suggested_parent") or _get_suggested_parent(
        pending.get("all_node_candidates", [])
    )

    pending["proposed_node"] = {
        "node_id": "NEW_PENDING",
        "node_title": node_name,
        "similarity": 0.0,
        "level": 0,
    }
    pending["node_confidence"] = "new_node_requested"
    pending["new_node_flag"] = True
    pending["proposed_parent"] = suggested_parent
    store_pending_fact(session_id, pending)

    return _execute_approval(pending, session_id)


def _execute_approval(fact_data: dict, session_id: str) -> dict:
    """Write approved fact to knowledge_base and run post-store hooks.

    Args:
        fact_data: The full pending fact dict.
        session_id: Current session ID.

    Returns:
        Dict with action and confirmation text.
    """
    verbatim = fact_data["verbatim_text"]
    content_type = fact_data["content_type"]
    epistemic_status = fact_data["epistemic_status"]
    proposed_node = fact_data.get("proposed_node")
    node_id = proposed_node["node_id"] if proposed_node else None
    node_title = proposed_node["node_title"] if proposed_node else "Unmatched"
    section = node_id.split(".")[1] if node_id and "." in node_id and node_id != "NEW_PENDING" else None

    try:
        from services.rag_service import store, retrieve

        chunk_id = store(
            content=verbatim,
            source_type="ceo_doc",
            section=section,
            epistemic_status=epistemic_status,
            topic_tags=[content_type, f"node:{node_id or 'unmatched'}"],
            session_id=session_id,
            confidence=fact_data.get("content_confidence", 0.5),
            metadata={
                "content_type": content_type,
                "node_id": node_id,
                "node_title": node_title,
                "source": "feed_handler",
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "node_confidence": fact_data.get("node_confidence"),
                "new_node_flag": fact_data.get("new_node_flag", False),
                "proposed_parent": fact_data.get("proposed_parent"),
                "status": "pending_architecture_review" if fact_data.get("new_node_flag") else None,
            },
        )

        if chunk_id is None:
            clear_pending_fact(session_id)
            set_feed_state(session_id, None)
            return {
                "action": "deduplicated",
                "response_text": (
                    f"This fact already exists in the knowledge base (duplicate detected). "
                    f"Nothing new was stored."
                ),
            }

        _run_post_store_hooks(verbatim, chunk_id, epistemic_status, session_id)

        clear_pending_fact(session_id)

        remaining = fact_data.get("remaining_facts", [])
        if remaining:
            set_feed_state(session_id, None)
            next_result = _process_next_fact(remaining, session_id)
            confirmation = (
                f"Stored [{epistemic_status}] → {node_id or 'Unmatched'} ({node_title})\n\n"
                f"---\n\n"
                f"{next_result['response_text']}"
            )
            return {
                "action": "stored_with_next",
                "response_text": confirmation,
                "chunk_id": chunk_id,
            }

        set_feed_state(session_id, None)
        new_node_note = ""
        if fact_data.get("new_node_flag"):
            new_node_note = "\n(New node flagged for architecture review — not yet permanent.)"

        return {
            "action": "stored",
            "response_text": (
                f"Stored [{epistemic_status}] → {node_id or 'Unmatched'} ({node_title})"
                f"{new_node_note}"
            ),
            "chunk_id": chunk_id,
        }

    except Exception as e:
        logger.error("[FeedHandler] Storage failed: %s", e)
        set_feed_state(session_id, None)
        clear_pending_fact(session_id)
        return {
            "action": "error",
            "response_text": f"Storage failed: {e}. Your data was NOT stored. Please try again.",
        }


def _run_post_store_hooks(
    content: str, chunk_id: str, epistemic_status: str, session_id: str
) -> None:
    """Run post-storage hooks: contradiction check, negative knowledge, temporal scoring."""
    try:
        from services.rag_service import retrieve
        from services.rag_hooks import store_contradiction_resolution, store_negative_knowledge
        from services.temporal_decay import compute_final_score

        contradictions = retrieve(
            query=content,
            source_types=["ceo_doc", "conversation", "decision"],
            top_k=3,
            threshold=0.75,
        )

        for chunk in contradictions:
            if chunk.id == chunk_id:
                continue
            if chunk.similarity > 0.85 and chunk.epistemic_status != epistemic_status:
                store_contradiction_resolution(
                    contradiction=f"New fact conflicts with existing: '{chunk.content[:80]}'",
                    resolution=f"New input supersedes (approved by CEO): '{content[:80]}'",
                    reasoning="CEO directly submitted and approved newer data",
                    session_id=session_id,
                )
                logger.info(
                    "[FeedHandler] Contradiction resolved: new chunk %s vs existing %s",
                    chunk_id,
                    chunk.id,
                )
                break

        if epistemic_status == "CONFIRMED":
            invalidated = retrieve(
                query=content,
                source_types=["ceo_doc", "agent_insight"],
                epistemic_status=["ASSUMPTION"],
                top_k=3,
                threshold=0.8,
            )
            for chunk in invalidated:
                if chunk.id == chunk_id:
                    continue
                store_negative_knowledge(
                    what_failed=f"Assumption invalidated: '{chunk.content[:80]}'",
                    reason=f"CEO confirmed contradicting fact: '{content[:80]}'",
                    source="ceo_correction",
                    session_id=session_id,
                )
                logger.info(
                    "[FeedHandler] Assumption invalidated: %s", chunk.id
                )
                break

        score = compute_final_score(
            similarity=1.0,
            created_at=datetime.now(timezone.utc).isoformat(),
            epistemic_status=epistemic_status,
        )
        logger.debug("[FeedHandler] Stored chunk %s with temporal score: %.4f", chunk_id, score)

    except Exception as e:
        logger.error("[FeedHandler] Post-store hooks error (non-fatal): %s", e)


def _process_next_fact(remaining_facts: list[str], session_id: str) -> dict:
    """Process the next fact in the queue.

    Args:
        remaining_facts: List of remaining fact texts.
        session_id: Session ID.

    Returns:
        Dict with review block for the next fact.
    """
    if not remaining_facts:
        return {
            "action": "complete",
            "response_text": "All facts processed.",
        }

    next_text = remaining_facts[0]
    content_classification = classify_content_type(next_text)
    content_type = content_classification["content_type"]
    epistemic_status = EPISTEMIC_STATUS_BY_CONTENT_TYPE.get(content_type, "INFERRED")
    node_matches = match_bp_node(next_text)

    proposed_node = None
    node_confidence = "unmatched"
    if node_matches and node_matches[0]["similarity"] >= 0.6:
        proposed_node = node_matches[0]
        node_confidence = "matched"
    elif node_matches:
        proposed_node = node_matches[0]
        node_confidence = "low_confidence"

    fact_data = {
        "verbatim_text": next_text,
        "content_type": content_type,
        "content_confidence": content_classification["confidence"],
        "epistemic_status": epistemic_status,
        "proposed_node": proposed_node,
        "node_confidence": node_confidence,
        "all_node_candidates": node_matches[:3],
        "source_format": "queued",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "remaining_facts": remaining_facts[1:],
    }

    store_pending_fact(session_id, fact_data)
    set_feed_state(session_id, "FEED_AWAITING_APPROVAL")

    review_text = _format_review_block(fact_data, remaining=len(remaining_facts) - 1)
    return {
        "action": "awaiting_approval",
        "response_text": review_text,
    }


def _format_review_block(fact_data: dict, remaining: int = 0) -> str:
    """Format a pending fact as a review block for Alex.

    Shows different options depending on match confidence:
    - High confidence (>=0.6): simple approve/adjust/create
    - Low confidence (<0.6, >=0.3): show top 3 candidates with reasoning
    - Very low (<0.3): skip candidates, suggest parent for new node

    Args:
        fact_data: The full fact dict.
        remaining: Number of facts still queued.

    Returns:
        Formatted review string.
    """
    content_type = fact_data["content_type"]
    confidence = fact_data.get("content_confidence", 0.0)
    epistemic_status = fact_data["epistemic_status"]
    proposed_node = fact_data.get("proposed_node")
    node_confidence = fact_data.get("node_confidence", "unmatched")
    verbatim = fact_data["verbatim_text"]
    candidates = fact_data.get("all_node_candidates", [])

    lines = []
    lines.append("ADD — Review before storing")
    lines.append("=" * 40)
    lines.append("")
    lines.append(f"  Text: \"{verbatim}\"")
    lines.append("")
    lines.append(f"  Type: {content_type.upper()}" + (
        " (low confidence)" if confidence < 0.7 else ""
    ))
    lines.append(f"  Status: [{epistemic_status}]")
    lines.append("")

    if node_confidence == "matched" and proposed_node:
        lines.append(
            f"  Node: {proposed_node['node_id']} — {proposed_node['node_title']}"
            f" ({int(proposed_node['similarity'] * 100)}% match)"
        )
        lines.append("")
        lines.append("-" * 40)
        lines.append("  [1] Approve — store as shown")
        lines.append("  [2] Adjust — pick a different node")
        lines.append("  [3] Create new node — flag for architecture review")
        lines.append("  [skip] — discard this fact")

    elif candidates and candidates[0].get("similarity", 0) >= 0.3:
        lines.append("  No strong match found. Closest candidates:")
        lines.append("")
        for i, c in enumerate(candidates[:3], 1):
            pct = int(c["similarity"] * 100)
            purpose = c.get("purpose", "")
            lines.append(f"  [{i}] {c['node_id']} — {c['node_title']} ({pct}% match)")
            if purpose:
                lines.append(f"      Purpose: {purpose[:100]}")
            lines.append("")

        suggested_parent = fact_data.get("suggested_parent") or _get_suggested_parent(candidates)
        fact_data["suggested_parent"] = suggested_parent
        lines.append(
            f"  If none fit, a new node could sit under: "
            f"{suggested_parent['node_id']} — {suggested_parent['node_title']}"
        )
        lines.append("")
        lines.append("-" * 40)
        lines.append(f"  [1] Use {candidates[0]['node_title']}")
        if len(candidates) > 1:
            lines.append(f"  [2] Use {candidates[1]['node_title']}")
        if len(candidates) > 2:
            lines.append(f"  [3] Use {candidates[2]['node_title']}")
        lines.append(f"  [4] Create new node under {suggested_parent['node_id']}")
        lines.append("  [5] Pick a different parent — I'll tell you which")
        lines.append("  [skip] — discard this fact")

    else:
        suggested_parent = fact_data.get("suggested_parent") or _get_suggested_parent(candidates)
        fact_data["suggested_parent"] = suggested_parent
        lines.append(
            "  This doesn't match anything in the current architecture."
        )
        lines.append(
            f"  Suggested parent: {suggested_parent['node_id']} — {suggested_parent['node_title']}"
        )
        lines.append("")
        lines.append("-" * 40)
        lines.append(f"  [1] Create new node under {suggested_parent['node_id']}")
        lines.append("  [2] Pick a different parent — I'll tell you which")
        lines.append("  [skip] — discard this fact")

    if remaining > 0:
        lines.append("")
        lines.append(f"  ({remaining} more fact(s) queued after this one)")

    return "\n".join(lines)


def handle_feed_message(text: str, session_id: str) -> str:
    """Main entry point for all feed workspace messages.

    Routes based on current feed state (approval flow or new input).

    Args:
        text: Message text from Alex.
        session_id: Current session ID.

    Returns:
        Response text for the chat. Never returns empty string.
    """
    try:
        state = get_feed_state(session_id)

        if state == "FEED_AWAITING_DUPLICATE_CONFIRM":
            result = _handle_duplicate_confirm(text, session_id)
            return result.get("response_text") or "Processing..."

        if state == "FEED_AWAITING_APPROVAL":
            result = handle_approval_response(text, session_id)
            return result.get("response_text") or "Processing your response..."

        if state == "FEED_AWAITING_NODE_SELECTION":
            result = handle_node_selection(text, session_id)
            return result.get("response_text") or "Processing node selection..."

        if state == "FEED_AWAITING_NEW_NODE_NAME":
            result = handle_new_node_name(text, session_id)
            return result.get("response_text") or "Processing new node..."

        if state == "FEED_AWAITING_PARENT_SELECTION":
            result = handle_parent_selection(text, session_id)
            return result.get("response_text") or "Processing parent selection..."

        result = handle_raw_text(text, session_id=session_id)
        return result.get("response_text") or "Input received but no facts could be extracted. Try rephrasing."
    except Exception as e:
        logger.error("[FeedHandler] Unhandled error in handle_feed_message: %s", e, exc_info=True)
        return f"Feed processing error: {e}. Please try again."


# --- Format detection and splitting (unchanged from original) ---


def detect_format(text: str) -> str:
    """Detect the format of raw text input.

    Args:
        text: The raw text.

    Returns:
        One of: "bullets", "table", "paragraph", "mixed".
    """
    lines = text.strip().split("\n")
    lines = [l for l in lines if l.strip()]

    if not lines:
        return "paragraph"

    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    bullet_lines = sum(1 for l in lines if bullet_pattern.match(l))

    separator_pattern = re.compile(r"[|\t]")
    table_lines = sum(1 for l in lines if separator_pattern.search(l))

    total = len(lines)

    if table_lines > total * 0.5:
        return "table"
    elif bullet_lines > total * 0.5:
        return "bullets"
    elif bullet_lines > 0 and (total - bullet_lines) > 2:
        return "mixed"
    else:
        return "paragraph"


def split_into_atomic_facts(text: str, fmt: str) -> list[dict]:
    """Split text into atomic facts based on detected format.

    Args:
        text: The raw text.
        fmt: Detected format (bullets/table/paragraph/mixed).

    Returns:
        List of dicts with: text, inferred_status, source_format.
    """
    if fmt == "bullets":
        return _split_bullets(text)
    elif fmt == "table":
        return _split_table(text)
    elif fmt == "mixed":
        return _split_mixed(text)
    else:
        return _split_paragraph(text)


def _split_bullets(text: str) -> list[dict]:
    """Parse bullet-point text into individual facts."""
    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    lines = text.strip().split("\n")
    facts = []

    for line in lines:
        cleaned = bullet_pattern.sub("", line).strip()
        if cleaned and len(cleaned) > 3:
            facts.append({
                "text": cleaned,
                "inferred_status": _infer_epistemic_status(cleaned),
                "source_format": "bullets",
            })

    return facts


def _split_table(text: str) -> list[dict]:
    """Parse table-formatted text into individual facts."""
    lines = text.strip().split("\n")
    facts = []

    separator = "\t" if "\t" in text else "|"
    header = None

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^[\s\-|+]+$", line):
            continue

        cells = [c.strip() for c in line.split(separator) if c.strip()]

        if header is None and cells:
            header = cells
            continue

        if cells and header:
            row_text = "; ".join(
                f"{header[j]}: {cells[j]}" if j < len(header) else cells[j]
                for j in range(len(cells))
            )
            if len(row_text) > 5:
                facts.append({
                    "text": row_text,
                    "inferred_status": _infer_epistemic_status(row_text),
                    "source_format": "table",
                })
        elif cells:
            row_text = "; ".join(cells)
            if len(row_text) > 5:
                facts.append({
                    "text": row_text,
                    "inferred_status": _infer_epistemic_status(row_text),
                    "source_format": "table",
                })

    return facts


def _split_paragraph(text: str) -> list[dict]:
    """Split paragraph text into sentences as atomic facts."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    facts = []

    for sent in sentences:
        sent = sent.strip()
        if len(sent) > 10:
            facts.append({
                "text": sent,
                "inferred_status": _infer_epistemic_status(sent),
                "source_format": "paragraph",
            })

    return facts


def _split_mixed(text: str) -> list[dict]:
    """Split mixed-format text (bullets + paragraphs)."""
    lines = text.strip().split("\n")
    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    facts = []
    paragraph_buffer = []

    for line in lines:
        if bullet_pattern.match(line):
            if paragraph_buffer:
                para_text = " ".join(paragraph_buffer)
                facts.extend(_split_paragraph(para_text))
                paragraph_buffer = []
            cleaned = bullet_pattern.sub("", line).strip()
            if cleaned and len(cleaned) > 3:
                facts.append({
                    "text": cleaned,
                    "inferred_status": _infer_epistemic_status(cleaned),
                    "source_format": "mixed_bullet",
                })
        elif line.strip():
            paragraph_buffer.append(line.strip())

    if paragraph_buffer:
        para_text = " ".join(paragraph_buffer)
        facts.extend(_split_paragraph(para_text))

    return facts


def _infer_epistemic_status(text: str) -> str:
    """Infer epistemic status from language cues in the text.

    Args:
        text: A single fact/claim.

    Returns:
        One of: CONFIRMED, ASSUMPTION, CONTRADICTION, INFERRED.
    """
    text_lower = text.lower()

    confirmed_cues = [
        "confirmed", "verified", "proven", "validated", "they said",
        "the contract states", "we know", "evidence shows", "data shows",
    ]
    assumption_cues = [
        "i think", "maybe", "hypothesis", "likely", "probably",
        "we assume", "should be", "expected to", "might", "could be",
        "we believe", "our assumption",
    ]
    contradiction_cues = [
        "contradicts", "conflicts with", "inconsistent",
        "but earlier", "however", "on the other hand",
    ]

    for cue in contradiction_cues:
        if cue in text_lower:
            return "CONTRADICTION"

    for cue in confirmed_cues:
        if cue in text_lower:
            return "CONFIRMED"

    for cue in assumption_cues:
        if cue in text_lower:
            return "ASSUMPTION"

    return "INFERRED"


# --- Legacy format functions (kept for backward compatibility) ---


def format_feed_response(results: dict) -> str:
    """Format the feed processing results as a chat message.

    Args:
        results: Output from handle_raw_text() or handle_feed_message().

    Returns:
        Formatted string for the chat panel.
    """
    if "response_text" in results:
        return results["response_text"]

    facts = results.get("facts", [])
    count = results.get("count", 0)
    fmt = results.get("format_detected", "unknown")

    if count == 0:
        return "No extractable facts found in that input. Try rephrasing or adding more detail."

    lines = [f"Extracted {count} fact(s) from {fmt} input:"]
    lines.append("")

    status_counts = {}
    for fact in facts:
        status = fact.get("inferred_status", "INFERRED")
        status_counts[status] = status_counts.get(status, 0) + 1

    for i, fact in enumerate(facts[:10], 1):
        status_tag = f"[{fact.get('inferred_status', 'INFERRED')}]"
        lines.append(f"  {i}. {status_tag} {fact['text'][:100]}")

    if count > 10:
        lines.append(f"  ... and {count - 10} more")

    lines.append("")
    lines.append("Epistemic breakdown: " + ", ".join(
        f"{status}: {c}" for status, c in sorted(status_counts.items())
    ))

    return "\n".join(lines)
