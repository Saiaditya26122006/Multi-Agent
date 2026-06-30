"""
RAG Hooks — integration points that store dynamic knowledge into the RAG.

Called by Mother Agent, L3 agent, and child agents at key lifecycle points:
- After agent completes → store insights (Layer 5)
- On Kill decision → store negative knowledge (Layer 6)
- On contradiction resolution → store resolution (Layer 10)
- After pipeline run → store run metadata (Layer 11)
- After web search → store external research (Layer 8)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def store_agent_insight(
    agent_name: str,
    insight: str,
    section: Optional[str] = None,
    run_id: Optional[str] = None,
    confidence: float = 0.6,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a key insight discovered by an agent during pipeline execution.

    Called after each child agent completes. The insight should be a
    distilled finding, not the full output.

    Args:
        agent_name: Which agent produced this insight.
        insight: The key finding (1-2 sentences).
        section: Business plan section number.
        run_id: Pipeline run ID.
        confidence: How confident the agent is in this insight.
        metadata: Additional context.

    Returns:
        Chunk ID if stored.
    """
    from services.rag_service import store

    content = f"Agent insight ({agent_name}): {insight}"

    return store(
        content=content,
        source_type="agent_insight",
        section=section,
        agent_name=agent_name,
        run_id=run_id,
        confidence=confidence,
        topic_tags=["agent-insight", agent_name],
        metadata=metadata,
    )


def store_negative_knowledge(
    what_failed: str,
    reason: str,
    source: str = "kill_decision",
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store knowledge about what NOT to do or suggest.

    Called when:
    - Alex kills a proposal
    - An approach repeatedly fails
    - An agent output is rejected as invalid

    Args:
        what_failed: Description of the idea/approach that failed.
        reason: Why it failed or was rejected.
        source: "kill_decision", "repeated_failure", "invalid_output".
        run_id: Pipeline run ID.
        session_id: Session ID.
        metadata: Additional context.

    Returns:
        Chunk ID if stored.
    """
    from services.rag_service import store

    content = (
        f"NEGATIVE KNOWLEDGE: {what_failed}. "
        f"Reason: {reason}. Source: {source}."
    )

    return store(
        content=content,
        source_type="negative_knowledge",
        session_id=session_id,
        run_id=run_id,
        topic_tags=["negative-knowledge", source],
        metadata={
            **(metadata or {}),
            "what_failed": what_failed,
            "reason": reason,
            "source": source,
        },
    )


def store_contradiction_resolution(
    contradiction: str,
    resolution: str,
    reasoning: str,
    affects_sections: Optional[list[str]] = None,
    affects_agents: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a CEO's resolution of a known contradiction.

    Once stored, Devil's Advocate should skip raising this contradiction.

    Args:
        contradiction: The contradiction that was resolved (e.g., "B2B vs B2C").
        resolution: What was decided.
        reasoning: Alex's reasoning.
        affects_sections: Which BP sections this affects.
        affects_agents: Which agents should know about this.
        session_id: Session ID.
        metadata: Additional context.

    Returns:
        Chunk ID if stored.
    """
    from services.rag_service import store

    content = (
        f"CONTRADICTION RESOLVED: {contradiction}. "
        f"Resolution: {resolution}. "
        f"CEO reasoning: {reasoning}."
    )

    tags = ["contradiction-resolved", contradiction.lower().replace(" ", "-")]
    if affects_agents:
        tags.extend(affects_agents)

    return store(
        content=content,
        source_type="contradiction_resolution",
        session_id=session_id,
        epistemic_status="CONFIRMED",
        topic_tags=tags,
        metadata={
            **(metadata or {}),
            "contradiction": contradiction,
            "resolution": resolution,
            "reasoning": reasoning,
            "affects_sections": affects_sections or [],
            "affects_agents": affects_agents or [],
        },
    )


def store_run_metadata(
    run_id: str,
    idea: str,
    sections_completed: list[str],
    sections_failed: Optional[dict] = None,
    alex_verdict: Optional[str] = None,
    alex_feedback: Optional[str] = None,
    total_tokens: Optional[int] = None,
    duration_seconds: Optional[float] = None,
    quality_scores: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a summary of a completed pipeline run.

    Called by Mother Agent after the full pipeline finishes.

    Args:
        run_id: Unique run identifier.
        idea: The business idea that was processed.
        sections_completed: List of section numbers that completed.
        sections_failed: Dict of {section: failure_reason}.
        alex_verdict: "yes", "adjust", "kill", or None if pending.
        alex_feedback: Alex's feedback text.
        total_tokens: Total tokens consumed.
        duration_seconds: Wall-clock time.
        quality_scores: Dict of quality metrics.
        metadata: Additional context.

    Returns:
        Chunk ID if stored.
    """
    from services.rag_service import store

    parts = [
        f"PIPELINE RUN [{run_id}]: idea='{idea}'",
        f"completed={','.join(sections_completed)}",
    ]

    if sections_failed:
        failed_str = "; ".join(f"{k}: {v}" for k, v in sections_failed.items())
        parts.append(f"failed=[{failed_str}]")

    if alex_verdict:
        parts.append(f"verdict={alex_verdict.upper()}")

    if alex_feedback:
        parts.append(f"feedback='{alex_feedback}'")

    if quality_scores:
        scores_str = ", ".join(f"{k}={v}" for k, v in quality_scores.items())
        parts.append(f"quality=[{scores_str}]")

    content = ". ".join(parts)

    return store(
        content=content,
        source_type="run_metadata",
        run_id=run_id,
        topic_tags=["run-metadata", idea.lower().replace(" ", "-")[:30]],
        metadata={
            **(metadata or {}),
            "idea": idea,
            "sections_completed": sections_completed,
            "sections_failed": sections_failed or {},
            "alex_verdict": alex_verdict,
            "alex_feedback": alex_feedback,
            "total_tokens": total_tokens,
            "duration_seconds": duration_seconds,
            "quality_scores": quality_scores or {},
        },
    )


def store_external_research(
    query: str,
    results_summary: str,
    source_urls: Optional[list[str]] = None,
    section: Optional[str] = None,
    agent_name: str = "environment_research",
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store external research results (web search, market data).

    Cached with stale_after_90_days policy to avoid re-searching.

    Args:
        query: The search query that was run.
        results_summary: Summary of what was found.
        source_urls: URLs of sources.
        section: Relevant BP section.
        agent_name: Which agent performed the research.
        metadata: Additional context.

    Returns:
        Chunk ID if stored.
    """
    from services.rag_service import store

    content = f"External research [{query}]: {results_summary}"

    return store(
        content=content,
        source_type="external_research",
        section=section,
        agent_name=agent_name,
        freshness_policy="stale_after_90_days",
        topic_tags=["external-research", agent_name],
        metadata={
            **(metadata or {}),
            "query": query,
            "source_urls": source_urls or [],
        },
    )


def check_negative_knowledge(proposal: str, threshold: float = 0.65) -> Optional[str]:
    """Check if a proposal conflicts with stored negative knowledge.

    Call this BEFORE dispatching a task to an agent.

    Args:
        proposal: Description of what you're about to propose/do.
        threshold: Similarity threshold for matching.

    Returns:
        The matching negative knowledge content if found, None otherwise.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query=proposal,
        source_types=["negative_knowledge"],
        top_k=3,
        threshold=threshold,
    )

    if chunks:
        return chunks[0].content

    return None


def check_resolved_contradictions(contradiction: str) -> Optional[str]:
    """Check if a contradiction has already been resolved.

    Called by Devil's Advocate before raising a contradiction.

    Args:
        contradiction: The contradiction to check.

    Returns:
        The resolution content if found, None otherwise.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query=f"contradiction resolved {contradiction}",
        source_types=["contradiction_resolution"],
        top_k=3,
        threshold=0.6,
    )

    if chunks:
        return chunks[0].content

    return None
