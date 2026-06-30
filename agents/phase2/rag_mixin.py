"""
RAG Mixin — provides active RAG retrieval for child agents.

Import and call `rag_enrich()` inside any agent's handle_request() to get
relevant context from the RAG knowledge base mid-execution.

Usage in any agent:
    from agents.phase2.rag_mixin import rag_enrich

    async def handle_request(self, ...):
        rag_context = rag_enrich("pricing decisions WTP", section="12")
        # Use rag_context in prompt building
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_rag_available = None


def _check_rag() -> bool:
    """Lazy check if RAG service is importable and functional."""
    global _rag_available
    if _rag_available is not None:
        return _rag_available
    try:
        from services.rag_service import retrieve
        _rag_available = True
    except Exception:
        _rag_available = False
    return _rag_available


def rag_enrich(
    query: str,
    section: Optional[str] = None,
    source_types: Optional[list[str]] = None,
    top_k: int = 5,
    max_chars: int = 1500,
) -> str:
    """Retrieve relevant RAG context for an agent's current task.

    Args:
        query: What the agent needs to know (e.g., "pricing decisions alex").
        section: Filter to specific BP section.
        source_types: Filter source types. Defaults to all useful types.
        top_k: Number of chunks to retrieve.
        max_chars: Max chars for the formatted output.

    Returns:
        Formatted string of relevant context, or empty string if RAG unavailable.
    """
    if not _check_rag():
        return ""

    try:
        from services.rag_service import retrieve, format_chunks_for_injection

        if source_types is None:
            source_types = [
                "ceo_doc", "conversation", "decision",
                "correction", "agent_insight", "negative_knowledge",
            ]

        chunks = retrieve(
            query=query,
            source_types=source_types,
            section=section,
            top_k=top_k,
            threshold=0.4,
            recency_boost=True,
        )

        if not chunks:
            return ""

        return format_chunks_for_injection(chunks, max_chars=max_chars)

    except Exception as e:
        logger.debug("[RAGMixin] Retrieval failed (non-critical): %s", e)
        return ""


def rag_check_killed(proposal: str) -> Optional[str]:
    """Check if a similar proposal was killed by the CEO.

    Args:
        proposal: Brief description of what the agent is about to propose.

    Returns:
        Warning string if killed, None otherwise.
    """
    if not _check_rag():
        return None

    try:
        from services.rag_hooks import check_negative_knowledge
        return check_negative_knowledge(proposal)
    except Exception:
        return None
