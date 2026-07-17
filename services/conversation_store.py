"""
Conversation Store — captures all CEO interactions into the RAG knowledge base.

Every message Alex sends, every question the system asks, every decision made,
and every correction — all flow into the vector store as retrievable memory.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def store_ceo_message(
    message: str,
    session_id: Optional[str] = None,
    channel: str = "telegram",
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a message from the CEO (Alex) into the RAG knowledge base.

    Args:
        message: The raw message text from Alex.
        session_id: Current session ID.
        channel: "telegram" or "web".
        metadata: Additional context (chat_id, message_id, etc.).

    Returns:
        Chunk ID if stored, None otherwise.
    """
    from services.rag_service import store

    if not message or not message.strip():
        return None

    if len(message.strip()) < 5:
        return None

    return store(
        content=f"CEO said: {message}",
        source_type="conversation",
        session_id=session_id,
        topic_tags=["ceo-message", channel],
        metadata={**(metadata or {}), "channel": channel, "role": "ceo"},
    )


def store_system_question(
    question: str,
    session_id: Optional[str] = None,
    agent_name: str = "l1_agent",
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a question the system asked the CEO.

    Args:
        question: The question text sent to Alex.
        session_id: Current session ID.
        agent_name: Which agent asked the question.
        metadata: Additional context.

    Returns:
        Chunk ID if stored, None otherwise.
    """
    from services.rag_service import store

    return store(
        content=f"System asked CEO: {question}",
        source_type="conversation",
        session_id=session_id,
        agent_name=agent_name,
        topic_tags=["system-question", agent_name],
        metadata={**(metadata or {}), "role": "system", "agent": agent_name},
    )


def store_ceo_answer(
    question: str,
    answer: str,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a CEO answer paired with the question for context.

    Args:
        question: The original question.
        answer: Alex's answer.
        session_id: Current session ID.
        metadata: Additional context.

    Returns:
        Chunk ID if stored, None otherwise.
    """
    from services.rag_service import store

    content = f"Q: {question} | CEO answered: {answer}"

    return store(
        content=content,
        source_type="conversation",
        session_id=session_id,
        topic_tags=["ceo-answer", "qa-pair"],
        metadata={**(metadata or {}), "question": question, "role": "ceo"},
    )


def store_decision(
    proposal: str,
    decision: str,
    reasoning: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a CEO decision (Yes/Adjust/Kill) with context.

    Args:
        proposal: What was proposed to Alex.
        decision: "yes", "adjust", or "kill".
        reasoning: Alex's reasoning for the decision.
        session_id: Current session ID.
        run_id: Pipeline run ID if applicable.
        metadata: Additional context.

    Returns:
        Chunk ID if stored, None otherwise.
    """
    from services.rag_service import store

    decision_lower = decision.lower().strip()
    content = f"DECISION [{decision_lower.upper()}]: Proposal: {proposal}"
    if reasoning:
        content += f" | CEO reasoning: {reasoning}"

    source_type = "decision"
    if decision_lower == "kill":
        source_type = "negative_knowledge"

    return store(
        content=content,
        source_type=source_type,
        session_id=session_id,
        run_id=run_id,
        topic_tags=["decision", decision_lower],
        metadata={
            **(metadata or {}),
            "proposal": proposal,
            "decision": decision_lower,
            "reasoning": reasoning,
        },
    )


def store_correction(
    original_fact: str,
    corrected_fact: str,
    session_id: Optional[str] = None,
    original_chunk_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a CEO correction (overrides previous knowledge).

    If original_chunk_id is provided, marks the old chunk as superseded.

    Args:
        original_fact: What was previously believed.
        corrected_fact: What Alex says is correct now.
        session_id: Current session ID.
        original_chunk_id: UUID of the chunk being corrected (if known).
        metadata: Additional context.

    Returns:
        Chunk ID of the new corrected fact.
    """
    from services.rag_service import store, supersede

    content = f"CORRECTION: Was: {original_fact} → Now: {corrected_fact}"

    new_id = store(
        content=content,
        source_type="correction",
        session_id=session_id,
        topic_tags=["correction", "override"],
        metadata={
            **(metadata or {}),
            "original_fact": original_fact,
            "corrected_fact": corrected_fact,
        },
    )

    if new_id and original_chunk_id:
        supersede(original_chunk_id, new_id)
        logger.info(
            "[ConvStore] Correction superseded %s → %s",
            original_chunk_id,
            new_id,
        )

    return new_id


def store_feedback(
    output_summary: str,
    feedback: str,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store CEO feedback on a pipeline output.

    Args:
        output_summary: Brief description of what was produced.
        feedback: Alex's feedback text.
        session_id: Current session ID.
        run_id: Pipeline run ID.
        agent_name: Which agent produced the output.
        metadata: Additional context.

    Returns:
        Chunk ID if stored, None otherwise.
    """
    from services.rag_service import store

    content = f"CEO feedback on {agent_name or 'pipeline'} output: {feedback}"
    if output_summary:
        content = f"Output: {output_summary} | {content}"

    return store(
        content=content,
        source_type="feedback",
        session_id=session_id,
        run_id=run_id,
        agent_name=agent_name,
        topic_tags=["feedback", agent_name or "pipeline"],
        metadata={
            **(metadata or {}),
            "output_summary": output_summary,
            "feedback": feedback,
        },
    )


def has_been_asked(question_topic: str, session_id: Optional[str] = None) -> bool:
    """Check if we've already asked Alex about a specific topic.

    Used to prevent re-asking questions the system already knows the answer to.

    Args:
        question_topic: Topic to search for in past questions/answers.
        session_id: Limit search to a specific session (optional).

    Returns:
        True if a similar question was already asked and answered.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query=question_topic,
        source_types=["conversation"],
        top_k=3,
        threshold=0.7,
    )

    for chunk in chunks:
        if "CEO answered:" in chunk.content or "CEO said:" in chunk.content:
            return True

    return False


# Measured against real stored kill decisions: rephrasings of a killed idea score
# 0.39-0.65, unrelated proposals score 0.10-0.29. 0.35 sits inside that gap.
# The previous 0.65 was above the entire "same idea" range — even the exact killed
# wording scored 0.649 — so this guard never fired and killed ideas came back.
KILLED_SIMILARITY_THRESHOLD = 0.35


def has_been_killed(proposal_topic: str) -> bool:
    """Check if a similar proposal was previously killed by Alex.

    Args:
        proposal_topic: Topic to check against negative knowledge.

    Returns:
        True if a similar proposal was killed.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query=proposal_topic,
        source_types=["negative_knowledge"],
        top_k=3,
        threshold=KILLED_SIMILARITY_THRESHOLD,
    )

    return len(chunks) > 0
