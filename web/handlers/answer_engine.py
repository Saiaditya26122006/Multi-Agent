"""
Intelligent Answer Engine for EpistemicOS.

Replaces rigid system_awareness routing with universal multi-source retrieval.
Any question Alex asks → search plan → parallel execution → Sonnet synthesis → rich answer.
"""

import hashlib
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Question Detection ──────────────────────────────────────────────────────

_QUESTION_LEAD = re.compile(
    r"^\s*(who|what|when|where|why|how|which)\b"
    r"|^\s*(can|could|would|should|do|does|did|is|are|will)\s+(i|you|we|it|this|there|these)\b"
    r"|^\s*(show|tell|list|find|give|help)\s+me\b",
    re.IGNORECASE,
)

_ACTION_PATTERNS = [
    re.compile(r"^\s*(store|add|upload|save|submit|create|build|generate|export|download|validate|challenge)\b", re.IGNORECASE),
    re.compile(r"^\s*(yes|no|confirm|approve|reject|skip|cancel|back|menu|home)\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),  # menu key numbers
]

_WORKSPACE_COMMANDS = {
    "feed", "build", "inspect", "challenge", "validate", "export",
    "back", "menu", "home", "a", "b", "c", "d", "e",
}


def is_question(text: str) -> bool:
    """Detect whether text is a question Alex wants answered.

    Fast heuristic-only detection (<5ms). No LLM call.
    Returns False for commands, menu keys, data input, actions.
    """
    stripped = text.strip()

    if not stripped or len(stripped) < 4:
        return stripped.endswith("?")

    if stripped.lower() in _WORKSPACE_COMMANDS:
        return False

    for pattern in _ACTION_PATTERNS:
        if pattern.match(stripped):
            return False

    # Long text without "?" is likely data input for Feed
    if len(stripped) > 150 and not stripped.endswith("?"):
        return False

    if stripped.endswith("?"):
        return True

    if _QUESTION_LEAD.match(stripped):
        return True

    # Informational requests
    info_patterns = [
        r"\btell me\b", r"\bshow me\b", r"\bexplain\b",
        r"\bwhat('s| is| are)\b", r"\bhow (many|much|often)\b",
        r"\bcan you\b.*\b(tell|show|find|give)\b",
    ]
    for pat in info_patterns:
        if re.search(pat, stripped, re.IGNORECASE):
            return True

    return False


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SearchOperation:
    op_type: str  # "semantic" | "metadata" | "keyword" | "architecture"
    query: str
    params: dict = field(default_factory=dict)
    purpose: str = ""


@dataclass
class SearchResult:
    content: str
    source_type: str = ""
    node_id: Optional[str] = None
    node_title: Optional[str] = None
    similarity: Optional[float] = None
    epistemic_status: Optional[str] = None
    created_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    origin: str = ""


@dataclass
class AnswerResponse:
    answer: str
    confidence: str
    sources: list = field(default_factory=list)
    search_ops_run: int = 0
    total_results: int = 0


# ─── Search Planning (Haiku) ─────────────────────────────────────────────────

def plan_searches(question: str) -> list[SearchOperation]:
    """Use Haiku to generate search operations from the question."""
    from web.handlers.answer_engine_prompts import SEARCH_PLANNER_PROMPT

    try:
        from web.handlers.llm_helper import _get_client

        client = _get_client()
        model_id = os.getenv(
            "CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )

        response = client.converse(
            modelId=model_id,
            system=[{"text": SEARCH_PLANNER_PROMPT}],
            messages=[{"role": "user", "content": [{"text": question}]}],
            inferenceConfig={"maxTokens": 300},
        )

        raw = response["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        ops_data = json.loads(raw)
        if not isinstance(ops_data, list):
            raise ValueError("Expected JSON array")

        operations = []
        for op in ops_data[:4]:
            operations.append(SearchOperation(
                op_type=op.get("op_type", "semantic"),
                query=op.get("query", question),
                params=op.get("params", {}),
                purpose=op.get("purpose", ""),
            ))

        # Ensure at least one semantic search exists
        if not any(op.op_type == "semantic" for op in operations):
            operations.insert(0, SearchOperation(
                op_type="semantic", query=question, purpose="primary semantic search"
            ))

        logger.info("[AnswerEngine] Planned %d search ops for: %s", len(operations), question[:60])
        return operations

    except Exception as e:
        logger.warning("[AnswerEngine] Search planning failed, using fallback: %s", e)
        return [
            SearchOperation(op_type="semantic", query=question, purpose="fallback semantic"),
            SearchOperation(op_type="keyword", query=question, params={"terms": _extract_keywords(question)}, purpose="fallback keyword"),
        ]


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text for keyword search fallback."""
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "what", "how", "when",
                  "where", "which", "who", "can", "you", "me", "our", "we", "i", "my",
                  "tell", "show", "give", "about", "this", "that", "and", "or", "in",
                  "on", "at", "to", "for", "of", "with", "do", "does", "have", "has"}
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    keywords = [w for w in words if w not in stop_words]
    return keywords[:5]


# ─── Search Executors ─────────────────────────────────────────────────────────

def _exec_semantic(op: SearchOperation) -> list[SearchResult]:
    """Vector similarity search via rag_service.retrieve()."""
    from services.rag_service import retrieve

    source_types = op.params.get("source_types")
    top_k = op.params.get("top_k", 8)

    chunks = retrieve(
        query=op.query,
        source_types=source_types,
        top_k=top_k,
        threshold=0.25,
        recency_boost=True,
    )

    results = []
    for chunk in chunks:
        meta = chunk.metadata or {}
        results.append(SearchResult(
            content=chunk.content,
            source_type=chunk.source_type or "",
            node_id=meta.get("node_id") or chunk.section or "",
            node_title=meta.get("node_title") or "",
            similarity=chunk.similarity,
            epistemic_status=chunk.epistemic_status or "",
            created_at=str(chunk.created_at) if chunk.created_at else "",
            metadata=meta,
            origin="semantic",
        ))
    return results


def _exec_metadata(op: SearchOperation) -> list[SearchResult]:
    """Structured Supabase queries for counts, dates, node lists."""
    from services.rag_service import _get_supabase, TABLE_NAME

    supabase = _get_supabase()
    action = op.params.get("action", "recent_nodes")
    results = []

    if action == "recent_nodes":
        time_window = op.params.get("time_window_minutes", 10080)
        limit = op.params.get("limit", 20)
        cutoff = (datetime.utcnow() - timedelta(minutes=time_window)).isoformat()

        rows = (
            supabase.table(TABLE_NAME)
            .select("content, metadata, created_at, source_type")
            .gte("created_at", cutoff)
            .is_("superseded_by", "null")
            .not_.is_("metadata->>node_id", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []

        seen_nodes: dict = {}
        for row in rows:
            meta = row.get("metadata") or {}
            nid = meta.get("node_id", "")
            if nid and nid not in seen_nodes:
                seen_nodes[nid] = SearchResult(
                    content=f"Node {nid} ({meta.get('node_title', '')}) — last updated {row.get('created_at', '')}. "
                            f"Content: {(row.get('content') or '')[:150]}",
                    source_type=row.get("source_type", ""),
                    node_id=nid,
                    node_title=meta.get("node_title", ""),
                    similarity=0.8,
                    created_at=row.get("created_at", ""),
                    metadata=meta,
                    origin="metadata",
                )
        results = list(seen_nodes.values())

    elif action == "node_count":
        all_rows = (
            supabase.table(TABLE_NAME)
            .select("metadata")
            .not_.is_("metadata->>node_id", "null")
            .is_("superseded_by", "null")
            .limit(5000)
            .execute()
        ).data or []
        node_ids = set()
        for r in all_rows:
            nid = (r.get("metadata") or {}).get("node_id")
            if nid:
                node_ids.add(nid)
        results.append(SearchResult(
            content=f"Total distinct nodes with stored data: {len(node_ids)}",
            source_type="system_stat",
            similarity=0.9,
            origin="metadata",
        ))

    elif action == "facts_under_node":
        node_id = op.params.get("node_id", "")
        if node_id:
            rows = (
                supabase.table(TABLE_NAME)
                .select("content, metadata, created_at, source_type, epistemic_status")
                .eq("metadata->>node_id", node_id)
                .is_("superseded_by", "null")
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            ).data or []
            for row in rows:
                meta = row.get("metadata") or {}
                results.append(SearchResult(
                    content=row.get("content", ""),
                    source_type=row.get("source_type", ""),
                    node_id=node_id,
                    node_title=meta.get("node_title", ""),
                    similarity=0.85,
                    epistemic_status=row.get("epistemic_status", ""),
                    created_at=row.get("created_at", ""),
                    metadata=meta,
                    origin="metadata",
                ))

    elif action == "timeline":
        time_window = op.params.get("time_window_minutes", 10080)
        limit = op.params.get("limit", 30)
        cutoff = (datetime.utcnow() - timedelta(minutes=time_window)).isoformat()

        rows = (
            supabase.table(TABLE_NAME)
            .select("content, metadata, created_at, source_type")
            .gte("created_at", cutoff)
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        for row in rows:
            meta = row.get("metadata") or {}
            results.append(SearchResult(
                content=row.get("content", ""),
                source_type=row.get("source_type", ""),
                node_id=meta.get("node_id", ""),
                node_title=meta.get("node_title", ""),
                similarity=0.7,
                created_at=row.get("created_at", ""),
                metadata=meta,
                origin="metadata",
            ))

    elif action == "node_lookup":
        node_id = op.params.get("node_id", "")
        if node_id:
            rows = (
                supabase.table(TABLE_NAME)
                .select("content, metadata, created_at, source_type")
                .eq("metadata->>node_id", node_id)
                .is_("superseded_by", "null")
                .order("created_at", desc=True)
                .limit(5)
                .execute()
            ).data or []
            for row in rows:
                meta = row.get("metadata") or {}
                results.append(SearchResult(
                    content=row.get("content", ""),
                    source_type=row.get("source_type", ""),
                    node_id=node_id,
                    node_title=meta.get("node_title", ""),
                    similarity=0.9,
                    created_at=row.get("created_at", ""),
                    metadata=meta,
                    origin="metadata",
                ))

    return results


def _exec_keyword(op: SearchOperation) -> list[SearchResult]:
    """Full-text keyword search (ILIKE) on knowledge_base.content."""
    from services.rag_service import _get_supabase, TABLE_NAME

    supabase = _get_supabase()
    terms = op.params.get("terms", [])
    if not terms:
        terms = _extract_keywords(op.query)

    results = []
    for term in terms[:3]:
        rows = (
            supabase.table(TABLE_NAME)
            .select("content, metadata, created_at, source_type, epistemic_status")
            .ilike("content", f"%{term}%")
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        ).data or []
        for row in rows:
            meta = row.get("metadata") or {}
            results.append(SearchResult(
                content=row.get("content", ""),
                source_type=row.get("source_type", ""),
                node_id=meta.get("node_id", ""),
                node_title=meta.get("node_title", ""),
                similarity=0.6,
                epistemic_status=row.get("epistemic_status", ""),
                created_at=row.get("created_at", ""),
                metadata=meta,
                origin="keyword",
            ))

    return results


def _exec_architecture(op: SearchOperation) -> list[SearchResult]:
    """Architecture/hierarchy lookup from bp_architecture.json."""
    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"

    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("[AnswerEngine] Could not read architecture file: %s", e)
        return []

    nodes = [n for n in data.get("nodes", []) if n.get("node_id", "").startswith("BP.")]
    action = op.params.get("action", "list_top_level")
    results = []

    if action == "list_top_level":
        top_level = [n for n in nodes if n.get("level") == 1.0]
        summary_parts = []
        for n in top_level:
            summary_parts.append(f"{n['node_id']}: {n.get('node_title', '')}")
        results.append(SearchResult(
            content=f"Top-level domains ({len(top_level)} total):\n" + "\n".join(summary_parts),
            source_type="architecture",
            similarity=0.9,
            origin="architecture",
        ))

    elif action == "node_details":
        node_id = op.params.get("node_id", "")
        matching = [n for n in nodes if n.get("node_id") == node_id]
        if matching:
            n = matching[0]
            results.append(SearchResult(
                content=(
                    f"Node {n['node_id']} ({n.get('node_title', '')}): "
                    f"type={n.get('node_type', '')}, level={n.get('level', '')}, "
                    f"parent={n.get('parent_node', '')}, "
                    f"status={n.get('architecture_status', '')}. "
                    f"Purpose: {(n.get('purpose') or '')[:200]}"
                ),
                source_type="architecture",
                node_id=n["node_id"],
                node_title=n.get("node_title", ""),
                similarity=0.95,
                metadata={"node_type": n.get("node_type"), "level": n.get("level")},
                origin="architecture",
            ))

    elif action == "children_of":
        node_id = op.params.get("node_id", "")
        children = [n for n in nodes if n.get("parent_node") == node_id]
        if children:
            parts = [f"{n['node_id']}: {n.get('node_title', '')}" for n in children[:20]]
            results.append(SearchResult(
                content=f"Children of {node_id} ({len(children)} nodes):\n" + "\n".join(parts),
                source_type="architecture",
                node_id=node_id,
                similarity=0.9,
                origin="architecture",
            ))

    return results


# ─── Search Execution (Parallel) ─────────────────────────────────────────────

_EXECUTORS = {
    "semantic": _exec_semantic,
    "metadata": _exec_metadata,
    "keyword": _exec_keyword,
    "architecture": _exec_architecture,
}


def execute_searches(
    operations: list[SearchOperation],
    session_id: Optional[str] = None,
) -> list[SearchResult]:
    """Execute all search operations in parallel."""
    from tools.trace_emitter import emit_trace

    if session_id:
        op_summary = ", ".join(op.op_type for op in operations)
        emit_trace(session_id, "AnswerEngine", "searching", f"Running {len(operations)} searches: {op_summary}")

    all_results: list[SearchResult] = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for op in operations:
            exec_fn = _EXECUTORS.get(op.op_type)
            if exec_fn:
                future = executor.submit(exec_fn, op)
                futures[future] = op

        for future in as_completed(futures, timeout=5.0):
            op = futures[future]
            try:
                results = future.result(timeout=3.0)
                all_results.extend(results)
            except Exception as e:
                logger.warning("[AnswerEngine] Search op '%s' failed: %s", op.op_type, e)

    logger.info("[AnswerEngine] Executed %d ops, got %d total results", len(operations), len(all_results))
    return all_results


# ─── Result Processing ────────────────────────────────────────────────────────

def merge_and_deduplicate(results: list[SearchResult]) -> list[SearchResult]:
    """Deduplicate by content hash, keep highest-similarity version. Cap at 15."""
    seen: dict[str, SearchResult] = {}

    for r in results:
        content_hash = hashlib.md5(r.content.encode()[:500]).hexdigest()
        existing = seen.get(content_hash)
        if not existing or (r.similarity or 0) > (existing.similarity or 0):
            seen[content_hash] = r

    deduped = sorted(
        seen.values(),
        key=lambda x: x.similarity or 0,
        reverse=True,
    )
    return deduped[:15]


def assess_confidence(results: list[SearchResult]) -> str:
    """Determine answer confidence based on result quality."""
    if not results:
        return "insufficient"

    similarities = [r.similarity for r in results if r.similarity is not None]
    if not similarities:
        return "low"

    top_sim = max(similarities)
    high_count = sum(1 for s in similarities if s > 0.65)

    if high_count >= 3 or top_sim > 0.80:
        return "high"
    elif top_sim > 0.50:
        return "medium"
    elif top_sim > 0.30:
        return "low"
    else:
        return "insufficient"


# ─── Answer Synthesis (Sonnet) ────────────────────────────────────────────────

def synthesize_answer(
    question: str,
    results: list[SearchResult],
    confidence: str,
) -> AnswerResponse:
    """Synthesize a natural answer from search results using Sonnet."""
    from web.handlers.answer_engine_prompts import SYNTHESIS_PROMPT, INSUFFICIENT_DATA_TEMPLATE

    sources = [
        {
            "node_id": r.node_id or "",
            "node_title": r.node_title or "",
            "content_preview": r.content[:150] if r.content else "",
            "similarity": round(r.similarity, 2) if r.similarity else None,
            "created_at": r.created_at or "",
        }
        for r in results[:10]
    ]

    # For insufficient confidence, skip the expensive Sonnet call
    if confidence == "insufficient":
        partial = ""
        if results:
            closest = results[:3]
            partial_parts = ["**Closest matches found** (low relevance):\n"]
            for r in closest:
                node_label = f"_{r.node_id}_" if r.node_id else ""
                partial_parts.append(
                    f"- {node_label} {r.content[:100]}... "
                    f"({int((r.similarity or 0) * 100)}% match)"
                )
            partial = "\n".join(partial_parts)

        answer_text = INSUFFICIENT_DATA_TEMPLATE.format(partial_matches=partial)
        return AnswerResponse(
            answer=answer_text,
            confidence="insufficient",
            sources=sources,
            search_ops_run=0,
            total_results=len(results),
        )

    # Build context for Sonnet
    context_parts = []
    for i, r in enumerate(results[:12], 1):
        node_label = f"[{r.node_id}]" if r.node_id else ""
        title_label = f"({r.node_title})" if r.node_title else ""
        status_label = f"[{r.epistemic_status}]" if r.epistemic_status else ""
        date_label = f"— {r.created_at[:10]}" if r.created_at and len(r.created_at) >= 10 else ""
        context_parts.append(
            f"{i}. {node_label} {title_label} {status_label} {date_label}\n   {r.content}"
        )

    context_str = "\n\n".join(context_parts)

    user_message = (
        f"CONTEXT (retrieved from knowledge base):\n{context_str}\n\n"
        f"QUESTION: {question}\n\n"
        f"Answer the question based on the context above."
    )

    try:
        from web.handlers.llm_helper import _get_client

        client = _get_client()
        model_id = os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6-v1:0")

        response = client.converse(
            modelId=model_id,
            system=[{"text": SYNTHESIS_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 600},
        )

        answer_text = response["output"]["message"]["content"][0]["text"]
        logger.info(
            "[AnswerEngine] Sonnet synthesized (%d tokens in, %d out)",
            response.get("usage", {}).get("inputTokens", 0),
            response.get("usage", {}).get("outputTokens", 0),
        )

    except Exception as e:
        logger.error("[AnswerEngine] Sonnet synthesis failed: %s", e)
        # Fallback: format raw results
        parts = []
        for r in results[:5]:
            node = f"**{r.node_id}**" if r.node_id else ""
            parts.append(f"- {node} {r.content[:200]}")
        answer_text = "Here's what I found:\n\n" + "\n".join(parts)

    return AnswerResponse(
        answer=answer_text,
        confidence=confidence,
        sources=sources,
        search_ops_run=0,
        total_results=len(results),
    )


# ─── Main Entry Point ────────────────────────────────────────────────────────

def answer_question(text: str, session_id: Optional[str] = None) -> Optional[AnswerResponse]:
    """Main entry point. Returns AnswerResponse or None if not a question."""
    from tools.trace_emitter import emit_trace

    start_time = time.time()

    if not is_question(text):
        return None

    if session_id:
        emit_trace(session_id, "AnswerEngine", "planning", "Analyzing your question...")

    # Step 1: Plan searches
    operations = plan_searches(text)

    # Step 2: Execute searches in parallel
    raw_results = execute_searches(operations, session_id)

    # Step 3: Merge and deduplicate
    results = merge_and_deduplicate(raw_results)

    # Step 4: Assess confidence
    confidence = assess_confidence(results)

    if session_id:
        emit_trace(
            session_id, "AnswerEngine", "synthesizing",
            f"Found {len(results)} relevant results (confidence: {confidence}). Composing answer..."
        )

    # Step 5: Synthesize answer
    response = synthesize_answer(text, results, confidence)
    response.search_ops_run = len(operations)

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(
        "[AnswerEngine] Answered in %dms (confidence=%s, results=%d, ops=%d): %s",
        elapsed_ms, confidence, len(results), len(operations), text[:60],
    )

    return response
