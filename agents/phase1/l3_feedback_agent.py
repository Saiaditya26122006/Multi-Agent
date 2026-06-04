"""
L3 Feedback Agent
Generates concise summaries and decision questions based on research briefs.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from agents.phase1.llm_client import get_client

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from memory.supabase_client import (
    get_ceo_context,
    get_assumptions_for_session,
    get_decisions_for_session,
    get_messages_for_session,
    update_session_state,
    create_decision,
    log_event,
    save_agent_output,
    get_memory_profile
)
from config import (
    GEMINI_MODEL,
    GEMINI_FALLBACK_MODEL,
    MAX_RETRIES,
    RETRY_WAIT_SECONDS,
    STATE_NEEDS_CLARIFICATION,
    STATE_AWAITING_APPROVAL,
    AGENT_L3_FEEDBACK
)
from utils.retry import retry_with_fallback
from tools.trace_emitter import emit_trace

# Load environment variables
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# LLM client now uses Claude via Bedrock


def generate_feedback(
    session_id: str,
    research_brief: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    L3 Feedback Agent: Generate a summary and decision question based on clarification.

    Args:
        session_id: UUID of the session
        research_brief: Optional (deprecated - not used, kept for backward compatibility)

    Returns:
        Dict with:
            - summary (str): Full summary with context, risk, and decision
            - decision_id (str): ID of the created decision
            - session_id (str): UUID of the session
            - telegram_message (str): Clean formatted message for Telegram
    """

    print(f"[L3] Processing feedback for session {session_id}")

    # Step 1: Load CEO context card
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("[L3] ✗ No CEO context found")
        raise ValueError("CEO context not found in database")

    session_key = str(ceo_context.get("telegram_chat_id"))
    emit_trace(session_key, "L3", "loading_context", "Loading CEO context")
    print(f"[L3] ✓ Loaded CEO context: {ceo_context.get('name')}")

    # Step 2: Load memory profile
    ceo_id = ceo_context.get('id')
    memory_profile = get_memory_profile(ceo_id)
    emit_trace(session_key, "L3", "loading_memory", f"Loading memory profile ({len(memory_profile)} entries)", {"count": len(memory_profile)})
    print(f"[L3] ✓ Loaded {len(memory_profile)} memory entries")

    # Step 3: Load assumptions from THIS session (these are the CEO's actual answers)
    assumptions = get_assumptions_for_session(session_id)

    if not assumptions:
        print("[L3] ✗ No assumptions found for this session")
        raise ValueError("No assumptions found - cannot generate feedback")

    emit_trace(session_key, "L3", "loading_assumptions", f"Loading session assumptions ({len(assumptions)} found)", {"count": len(assumptions)})
    print(f"[L3] ✓ Loaded {len(assumptions)} assumptions from this session")

    # Step 4: Load pending decisions
    emit_trace(session_key, "L3", "loading_decisions", "Loading session decisions")
    decisions = get_decisions_for_session(session_id)
    print(f"[L3] ✓ Loaded {len(decisions)} decisions")

    # Step 5: Build context for the LLM from the actual conversation
    context_parts = []

    # CEO Context
    context_parts.append("=== CEO CONTEXT ===")
    context_parts.append(f"CEO: {ceo_context.get('name')} at {ceo_context.get('company')}")
    context_parts.append(f"Priorities: {ceo_context.get('strategic_priorities')}")
    context_parts.append(f"Constraints: {ceo_context.get('known_constraints')}")
    context_parts.append("")

    # Long-term Memory
    if memory_profile:
        context_parts.append("=== LONG-TERM MEMORY (from past sessions) ===")
        for memory in memory_profile[:5]:  # Show top 5 most relevant
            mem_type = memory.get("memory_type", "").replace("_", " ").title()
            content = memory.get("content")
            context_parts.append(f"[{mem_type}] {content}")
        context_parts.append("")

    # Conversation Context — actual messages from the session
    session_messages = get_messages_for_session(session_id)
    if session_messages:
        context_parts.append("=== FULL CONVERSATION THIS SESSION ===")
        for msg in session_messages:
            content = msg.get("content", "")
            context_parts.append(f"- {content}")
        context_parts.append("")

    # Assumptions (structured Q&A context)
    context_parts.append("=== CLARIFICATION Q&A (from L1 agent) ===")
    for i, assumption in enumerate(assumptions, 1):
        statement = assumption.get('statement', '')
        context_parts.append(f"{i}. {statement}")
    context_parts.append("")

    # Pending Decisions (if any)
    if decisions:
        pending = [d for d in decisions if d.get('status') == 'pending_approval']
        if pending:
            context_parts.append("=== PENDING DECISIONS ===")
            for decision in pending[:3]:  # Limit to 3
                context_parts.append(f"- {decision.get('decision')}")
            context_parts.append("")

    context = "\n".join(context_parts)

    # Step 5: Create prompt for Gemini
    emit_trace(session_key, "L3", "building_prompt", "Building feedback prompt")
    prompt = f"""You are a business strategy feedback agent. The CEO just finished answering 3 clarifying questions. Based on their answers, generate a concise summary.

{context}

IMPORTANT:
- The "WHAT THE CEO SAID" section contains the actual conversation context from THIS session
- The "LONG-TERM MEMORY" section contains validated facts from PAST sessions
- When relevant, reference past decisions: "This aligns with your earlier decision to..." or "Building on your Spain-first strategy..."
- Use ONLY these sources - do not invent or assume additional details

INSTRUCTIONS:
Create a short, plain-language summary with EXACTLY these three parts:

1. WHAT WE KNOW (1 paragraph, 2-3 sentences):
   - Summarize what the CEO communicated in their answers
   - Reference relevant long-term memory when it adds context
   - Focus on what they want to accomplish
   - Keep it concrete and specific to what they actually said

2. BIGGEST OPEN RISK (1 sentence):
   - Identify the single most important uncertainty or risk based on what the CEO shared
   - Focus on what's still unclear or could go wrong

3. DECISION QUESTION (1 sentence + options):
   - Ask if the CEO wants to proceed with what they described
   - Provide exactly these three reply options:
     • Yes - proceed as planned
     • Adjust - modify the approach
     • Kill - stop this initiative

FORMATTING RULES:
- Use simple, direct language
- No corporate jargon or buzzwords
- No markdown formatting (**, ##, etc.)
- Keep total length under 200 words
- Write for someone who is busy and wants clarity
- Stay true to what the CEO actually said - don't add external research or assumptions

OUTPUT:"""

    print("[L3] ✓ Generating feedback with Gemini...")

    # Step 6: Call Gemini to generate the summary with retry logic
    emit_trace(session_key, "L3", "calling_llm", "Generating feedback summary...", {"model": GEMINI_MODEL})
    import time as _time
    _llm_start = _time.time()

    @retry_with_fallback(max_retries=MAX_RETRIES, wait_seconds=RETRY_WAIT_SECONDS)
    def call_llm_with_retry():
        client = get_client()
        try:
            response = client.generate_content(
                prompt=prompt,
                system_instruction=None
            )
            return response.strip()
        except Exception as e:
            print(f"[L3] ✗ Claude API call failed: {str(e)}")
            raise

    summary = call_llm_with_retry()
    _llm_duration = round(_time.time() - _llm_start, 2)
    emit_trace(session_key, "L3", "llm_complete", "Summary generated", {"duration_s": _llm_duration, "chars": len(summary)})

    print(f"[L3] ✓ Generated summary: {len(summary)} chars")

    # Step 7: Create telegram message (clean version without markdown)
    emit_trace(session_key, "L3", "cleaning_response", "Formatting for Telegram")
    telegram_message = summary

    # Clean up any markdown that slipped through
    telegram_message = telegram_message.replace('**', '')
    telegram_message = telegram_message.replace('##', '')
    telegram_message = telegram_message.replace('###', '')

    # Step 8: Update session state to AWAITING_APPROVAL
    emit_trace(session_key, "L3", "updating_session", "Updating session to AWAITING_APPROVAL")
    updated_session = update_session_state(session_id, STATE_AWAITING_APPROVAL)

    if not updated_session:
        print("[L3] ⚠ Warning: Failed to update session state")

    print(f"[L3] ✓ Updated session state to {STATE_AWAITING_APPROVAL}")

    # Step 9: Create decision object
    emit_trace(session_key, "L3", "creating_decision", "Creating decision object in DB")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    decision_id = f"decision_{timestamp}"

    # Extract assumption IDs
    assumption_ids = [a.get('assumption_id') for a in assumptions if a.get('assumption_id')]

    # Build decision statement from first assumption (the original CEO message)
    first_assumption = assumptions[0].get('statement', '') if assumptions else ''
    decision_statement = f"Proceed with CEO's plan based on clarification"

    decision_obj = create_decision(
        decision_id=decision_id,
        decision=decision_statement,
        rationale=summary,
        session_id=session_id,
        assumptions_used=assumption_ids,
        evidence_used=[],  # No external research, only conversation
        sections_affected=[],
        status="pending_approval"
    )

    if not decision_obj:
        print("[L3] ✗ Failed to create decision")
        raise ValueError("Failed to create decision in database")

    print(f"[L3] ✓ Created decision: {decision_id}")

    # Step 10: Log the event
    event = log_event(
        agent_id=AGENT_L3_FEEDBACK,
        action=f"GENERATED_FEEDBACK: {decision_id}",
        session_id=session_id,
        state_before=STATE_NEEDS_CLARIFICATION,
        state_after=STATE_AWAITING_APPROVAL,
        input_ref=f"assumptions:{len(assumptions)}",
        output_ref=f"decision_id:{decision_id}"
    )

    if not event:
        print("[L3] ⚠ Warning: Failed to log event")

    print(f"[L3] ✓ Event logged")

    # Step 11: Save raw output to agent_outputs table
    output = save_agent_output(
        agent_id=AGENT_L3_FEEDBACK,
        session_id=session_id,
        output_text=summary,
        input_summary=f"Clarification with {len(assumptions)} Q&A exchanges"
    )

    if not output:
        print("[L3] ⚠ Warning: Failed to save agent output")

    print(f"[L3] ✓ Agent output saved")

    # Success!
    emit_trace(session_key, "L3", "complete", "Feedback ready, sent to Alex")
    print(f"[L3] ✅ Feedback generated successfully")
    return {
        "summary": summary,
        "decision_id": decision_id,
        "session_id": session_id,
        "telegram_message": telegram_message
    }


if __name__ == "__main__":
    # Test the L3 agent
    # First, we need to create a test research brief
    from memory.supabase_client import supabase

    test_session_id = "a5124a5d-6023-4dc4-951d-5d3cea448fa6"

    # Create a test research brief
    test_brief = {
        "research_id": "test_research_20260514",
        "topic": "Market expansion into Southeast Asia",
        "source_type": "system_structured",
        "key_findings": [
            "High mobile penetration (85%+) in target markets",
            "Strong demand for B2B SaaS solutions",
            "Regulatory approval required in 3 countries"
        ],
        "evidence_quality": "medium",
        "remaining_uncertainty": "Unclear timeline for regulatory approvals",
        "decision_relevance": "Critical for Q3 expansion plans",
        "session_id": test_session_id
    }

    # Insert test research brief
    try:
        supabase.table("research_briefs").insert(test_brief).execute()
        print("✓ Test research brief created")
    except Exception as e:
        print(f"Note: Test brief may already exist: {e}")

    print("=" * 60)
    print("L3 Feedback Agent - Test Run")
    print("=" * 60)

    result = generate_feedback(
        session_id=test_session_id,
        research_brief=test_brief
    )

    print("\n" + "=" * 60)
    print("Test Result:")
    print("=" * 60)
    print(f"Decision ID: {result['decision_id']}")
    print(f"Session ID: {result['session_id']}")
    print(f"\nSummary:\n{result['summary']}")
    print(f"\nTelegram Message:\n{result['telegram_message']}")
    print("=" * 60)
