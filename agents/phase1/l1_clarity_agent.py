"""
L1 Clarity Agent
Generates focused clarifying questions based on vague CEO messages.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any
from datetime import datetime
from dotenv import load_dotenv
from agents.phase1.llm_client import get_client

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from memory.supabase_client import (
    get_ceo_context,
    get_open_business_plan_sections,
    get_unresolved_assumptions,
    get_pending_decisions,
    create_assumption,
    update_session_state,
    log_event,
    get_assumptions_for_session,
    get_memory_profile,
    get_messages_for_session,
)
from config import (
    MAX_QUESTIONS,
    GEMINI_MODEL,
    GEMINI_FALLBACK_MODEL,
    MAX_RETRIES,
    RETRY_WAIT_SECONDS,
    STATE_NEEDS_CLARIFICATION,
    AGENT_L1_CLARITY
)
from utils.retry import retry_with_fallback
from tools.trace_emitter import emit_trace

# Load environment variables
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# LLM client now uses Claude via Bedrock


def generate_clarifying_question(
    session_id: str,
    ceo_id: str,
    message_text: str
) -> Dict[str, Any]:
    """
    L1 Clarity Agent: Generate one focused clarifying question.

    Args:
        session_id: UUID of the session
        ceo_id: UUID of the CEO
        message_text: The CEO's message text

    Returns:
        Dict with:
            - question (str): The clarifying question
            - assumption_id (str): ID of the created assumption
            - session_id (str): UUID of the session
            - clarification_complete (bool): True if 3 questions already asked
    """

    print(f"[L1] Processing message for session {session_id}")

    ceo_ctx = get_ceo_context()
    session_key = str(ceo_ctx.get("telegram_chat_id")) if ceo_ctx else ""

    # CRITICAL: Check question counter - maximum MAX_QUESTIONS per session
    existing_assumptions = get_assumptions_for_session(session_id)
    question_count = len(existing_assumptions)
    emit_trace(session_key, "L1", "checking_counter", f"Checking question count ({question_count}/{MAX_QUESTIONS})", {"count": question_count, "max": MAX_QUESTIONS})

    print(f"[L1] Questions asked so far: {question_count}/{MAX_QUESTIONS}")

    if question_count >= MAX_QUESTIONS:
        print(f"[L1] ✓ Maximum questions reached ({MAX_QUESTIONS}/{MAX_QUESTIONS})")
        print("[L1] ✓ Clarification phase complete, ready for L3")
        return {
            "clarification_complete": True,
            "session_id": session_id,
            "question": None,
            "assumption_id": None
        }

    # Step 1: Load CEO context card
    emit_trace(session_key, "L1", "loading_context", "Loading CEO context")
    ceo_context = ceo_ctx

    if not ceo_context:
        print("[L1] ✗ No CEO context found")
        raise ValueError("CEO context not found in database")

    print(f"[L1] ✓ Loaded CEO context: {ceo_context.get('name')}")

    # Step 2: Load memory profile
    memory_profile = get_memory_profile(ceo_id)
    emit_trace(session_key, "L1", "loading_memory", f"Loading memory profile ({len(memory_profile)} memories)", {"count": len(memory_profile)})
    print(f"[L1] ✓ Loaded {len(memory_profile)} memory entries")

    # Step 3: Load active project state (scoped to this session)
    open_sections = get_open_business_plan_sections(session_id=session_id)
    unresolved_assumptions = get_unresolved_assumptions(session_id=session_id)
    pending_decisions = get_pending_decisions(session_id=session_id)

    # Step 3b: Load conversation history for this session
    session_messages = get_messages_for_session(session_id)

    emit_trace(session_key, "L1", "loading_project_state", "Loading project state", {
        "open_sections": len(open_sections),
        "unresolved_assumptions": len(unresolved_assumptions),
        "pending_decisions": len(pending_decisions),
        "messages": len(session_messages),
    })

    print(f"[L1] ✓ Project state loaded:")
    print(f"     - Open sections: {len(open_sections)}")
    print(f"     - Unresolved assumptions (this session): {len(unresolved_assumptions)}")
    print(f"     - Pending decisions: {len(pending_decisions)}")
    print(f"     - Messages in session: {len(session_messages)}")

    # Step 4: Build context for the LLM
    context_parts = []

    # CEO Context
    context_parts.append("=== CEO CONTEXT ===")
    context_parts.append(f"Name: {ceo_context.get('name')}")
    context_parts.append(f"Company: {ceo_context.get('company')}")
    context_parts.append(f"Output Style: {ceo_context.get('output_style')}")
    context_parts.append(f"Strategic Priorities: {ceo_context.get('strategic_priorities')}")
    context_parts.append(f"Known Constraints: {ceo_context.get('known_constraints')}")
    context_parts.append("")

    # Long-term Memory (preferences only — strip project-specific content)
    if memory_profile:
        context_parts.append("=== CEO PREFERENCES (from past sessions) ===")
        for memory in memory_profile[:10]:
            mem_type = memory.get("memory_type", "").replace("_", " ").title()
            content = memory.get("content", "")
            # Strip memories that name specific past projects
            if any(kw in content.lower() for kw in [
                "epistemicos", "epistemic os", "papertrail", "paper trail",
            ]):
                continue
            context_parts.append(f"[{mem_type}] {content}")
        context_parts.append("")

    # Conversation history (what was asked and answered so far)
    if session_messages:
        context_parts.append("=== CONVERSATION THIS SESSION (questions asked + CEO answers) ===")
        for msg in session_messages:
            content = msg.get("content", "")
            context_parts.append(f"- {content}")
        context_parts.append("")

    # Previous questions in this session (from assumptions)
    if existing_assumptions:
        context_parts.append("=== QUESTIONS ALREADY ASKED THIS SESSION ===")
        for assumption in existing_assumptions:
            context_parts.append(f"- {assumption.get('statement')}")
        context_parts.append("")

    # Business Plan Sections
    if open_sections:
        context_parts.append("=== OPEN BUSINESS PLAN SECTIONS ===")
        for section in open_sections[:5]:
            context_parts.append(f"- {section.get('section_name')} (status: {section.get('status')})")
        context_parts.append("")

    context = "\n".join(context_parts)

    # Step 5: Create prompt
    current_question_number = question_count + 1

    system_prompt = (
        "You are a clarity agent. Your ONLY job is to ask clarifying questions "
        "about the CURRENT idea described below. You must NEVER mention, reference, "
        "or compare the current idea to any other company, product, or idea — "
        "including anything from the CEO's past sessions or memory. Treat the "
        "current idea as if it is the only idea that has ever existed. "
        "If memory mentions other projects, IGNORE them completely."
    )

    prompt = f"""This is question {current_question_number} of {MAX_QUESTIONS} maximum questions.

{context}

CEO'S LATEST MESSAGE: "{message_text}"

RULES:
1. This is question {current_question_number}/{MAX_QUESTIONS} - make it count
2. Ask ONE specific, focused question to clarify the CEO's intent
3. DO NOT ask about information already in the CEO context card
4. DO NOT ask about information already in CEO PREFERENCES
5. DO NOT repeat any question from "QUESTIONS ALREADY ASKED THIS SESSION"
6. DO NOT ask about things the CEO already answered in the conversation above
7. Focus on understanding what the CEO wants to accomplish
8. Keep the question short and direct (1 sentence)
9. Since you only have {MAX_QUESTIONS - question_count} questions left, prioritize the most critical missing info
10. NEVER reference any other company or idea the CEO has worked on before

OUTPUT FORMAT:
Return ONLY the question text, nothing else. No preamble, no explanation.

QUESTION:"""

    emit_trace(session_key, "L1", "building_prompt", "Building LLM prompt")
    print("[L1] ✓ Generating clarifying question with Gemini...")

    # Step 5: Call Gemini to generate the question with retry logic
    emit_trace(session_key, "L1", "calling_llm", "Generating clarifying question...", {"model": GEMINI_MODEL})
    import time as _time
    _llm_start = _time.time()

    @retry_with_fallback(max_retries=MAX_RETRIES, wait_seconds=RETRY_WAIT_SECONDS)
    def call_llm_with_retry():
        client = get_client()
        try:
            response = client.generate_content(
                prompt=prompt,
                system_instruction=system_prompt
            )
            return response.strip()
        except Exception as e:
            print(f"[L1] ✗ Claude API call failed: {str(e)}")
            raise

    raw_question = call_llm_with_retry()
    _llm_duration = round(_time.time() - _llm_start, 2)
    emit_trace(session_key, "L1", "llm_complete", "Question generated", {"duration_s": _llm_duration, "model": "claude-haiku"})

    # Add progress indicator to the question
    question = f"Question {current_question_number} of {MAX_QUESTIONS}: {raw_question}"

    print(f"[L1] ✓ Generated question: {question[:80]}...")

    # Step 6: Create an assumption based on the question
    emit_trace(session_key, "L1", "writing_assumption", "Writing assumption to database")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    assumption_id = f"assumption_{timestamp}"

    # The assumption is what we're assuming about the CEO's intent
    assumption_statement = f"Assuming the CEO's message '{message_text[:50]}...' requires clarification about: {question}"

    assumption = create_assumption(
        assumption_id=assumption_id,
        statement=assumption_statement,
        session_id=session_id,
        confidence="low",
        clarification_status="pending"
    )

    if not assumption:
        print("[L1] ✗ Failed to create assumption")
        raise ValueError("Failed to create assumption in database")

    print(f"[L1] ✓ Created assumption: {assumption_id}")

    # Step 7: Update session state to NEEDS_CLARIFICATION
    emit_trace(session_key, "L1", "updating_session", "Updating session state")
    updated_session = update_session_state(session_id, STATE_NEEDS_CLARIFICATION)

    if not updated_session:
        print("[L1] ⚠ Warning: Failed to update session state")
        # Continue anyway - session state update failure shouldn't block

    print(f"[L1] ✓ Updated session state to {STATE_NEEDS_CLARIFICATION}")

    # Step 8: Log the event
    event = log_event(
        agent_id=AGENT_L1_CLARITY,
        action=f"GENERATED_QUESTION: {question[:50]}...",
        session_id=session_id,
        state_before=None,
        state_after=STATE_NEEDS_CLARIFICATION,
        input_ref=f"message:{message_text[:50]}",
        output_ref=f"assumption_id:{assumption_id}"
    )

    if not event:
        print("[L1] ⚠ Warning: Failed to log event")
        # Continue anyway

    print(f"[L1] ✓ Event logged")

    # Success!
    emit_trace(session_key, "L1", "complete", f"Question {current_question_number}/{MAX_QUESTIONS} ready")
    print(f"[L1] ✅ Clarifying question generated successfully ({current_question_number}/3)")
    return {
        "question": question,
        "assumption_id": assumption_id,
        "session_id": session_id,
        "clarification_complete": False
    }


if __name__ == "__main__":
    # Test the L1 agent
    test_session_id = "a5124a5d-6023-4dc4-951d-5d3cea448fa6"  # Use existing session from L0 tests
    test_ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"
    test_message = "I want to grow the business"

    print("=" * 60)
    print("L1 Clarity Agent - Test Run")
    print("=" * 60)

    result = generate_clarifying_question(
        session_id=test_session_id,
        ceo_id=test_ceo_id,
        message_text=test_message
    )

    print("\n" + "=" * 60)
    print("Test Result:")
    print("=" * 60)
    for key, value in result.items():
        print(f"  {key}: {value}")
    print("=" * 60)
