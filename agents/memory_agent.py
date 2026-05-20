"""
Memory Agent
Consolidates session learnings into long-term memory and generates welcome-back messages.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.supabase_client import (
    get_ceo_context,
    get_assumptions_for_session,
    get_decisions_for_session,
    get_memory_profile,
    add_memory,
    get_recent_sessions,
    get_last_message_time,
    update_memory_last_referenced,
    get_active_session
)
from config.config import (
    GEMINI_MODEL,
    MAX_RETRIES,
    RETRY_WAIT_SECONDS
)
from utils.retry import retry_with_fallback

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env file")


def consolidate_session_memory(session_id: str, ceo_id: str) -> List[Dict[str, Any]]:
    """
    Extract and store long-term memories from a completed session.

    Called automatically when a session is marked COMPLETED.
    Analyzes assumptions and decisions to extract strategic insights.

    Args:
        session_id: UUID of the completed session
        ceo_id: UUID of the CEO

    Returns:
        List of created memory entries
    """
    print(f"\n[MEMORY] Consolidating session {session_id}...")

    # Step 1: Load session data
    assumptions = get_assumptions_for_session(session_id)
    decisions = get_decisions_for_session(session_id)

    if not assumptions and not decisions:
        print("[MEMORY] No assumptions or decisions found - skipping consolidation")
        return []

    print(f"[MEMORY] Found {len(assumptions)} assumptions, {len(decisions)} decisions")

    # Step 2: Build context for Gemini
    context_parts = []

    context_parts.append("=== SESSION ASSUMPTIONS (CEO's answers) ===")
    for assumption in assumptions:
        context_parts.append(f"- {assumption.get('statement')}")
    context_parts.append("")

    context_parts.append("=== DECISIONS MADE ===")
    for decision in decisions:
        context_parts.append(f"- {decision.get('decision')}")
        context_parts.append(f"  Rationale: {decision.get('rationale', 'N/A')[:200]}")
        context_parts.append(f"  Status: {decision.get('status')}")
    context_parts.append("")

    context = "\n".join(context_parts)

    # Step 3: Create prompt for Gemini
    prompt = f"""You are a memory extraction agent. Analyze this completed session and extract long-term memories.

{context}

TASK:
Extract distinct, actionable memories from this session. Each memory should be:
1. Specific and factual (not vague)
2. Useful for future conversations
3. Categorized correctly

MEMORY TYPES:
- strategic_decision: Key decisions made (e.g., "Focus on Spain market first")
- recurring_priority: Ongoing priorities mentioned (e.g., "Institutional sales over pilots")
- validated_assumption: Facts the CEO confirmed (e.g., "Closed pilot = signed agreement")
- key_contact: Names, companies, or specific partners mentioned
- market_insight: Market knowledge or constraints (e.g., "Spain regulatory approval needed")
- communication_pattern: How the CEO prefers to communicate or decide

OUTPUT FORMAT:
Return a JSON array of memories. Each memory must have:
{{
  "type": "memory_type_here",
  "content": "The extracted memory in one clear sentence",
  "confidence": "high" | "medium" | "low"
}}

RULES:
- Maximum 5 memories per session
- Each memory must be one sentence, under 200 characters
- Only extract what's explicitly stated or strongly implied
- Skip vague or generic insights
- Confidence = high (directly stated), medium (strongly implied), low (inferred)

OUTPUT:
Return ONLY the JSON array, no other text.

EXAMPLE OUTPUT:
[
  {{"type": "strategic_decision", "content": "Focus on Spain as first market for expansion.", "confidence": "high"}},
  {{"type": "recurring_priority", "content": "Prioritize institutional sales over pilot customers.", "confidence": "high"}},
  {{"type": "validated_assumption", "content": "Closed pilot means signed agreement with 3-month commitment.", "confidence": "high"}}
]

JSON ARRAY:"""

    print("[MEMORY] Extracting memories with Gemini...")

    # Step 4: Call Gemini to extract memories
    @retry_with_fallback(max_retries=MAX_RETRIES, wait_seconds=RETRY_WAIT_SECONDS)
    def call_gemini_with_retry():
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            print(f"[MEMORY] ✗ Gemini API call failed: {str(e)}")
            raise

    try:
        raw_response = call_gemini_with_retry()
        print(f"[MEMORY] Raw response: {raw_response[:200]}...")

        # Parse JSON response
        import json
        # Clean up response (remove markdown code blocks if present)
        json_text = raw_response
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0].strip()

        memories_data = json.loads(json_text)

        if not isinstance(memories_data, list):
            print(f"[MEMORY] ✗ Expected JSON array, got: {type(memories_data)}")
            return []

        # Step 5: Store each extracted memory
        created_memories = []
        for memory_item in memories_data[:5]:  # Max 5 memories
            memory_type = memory_item.get("type")
            content = memory_item.get("content")
            confidence = memory_item.get("confidence", "medium")

            if not memory_type or not content:
                print(f"[MEMORY] ⚠ Skipping invalid memory: {memory_item}")
                continue

            # Validate memory type
            valid_types = [
                "strategic_decision",
                "recurring_priority",
                "validated_assumption",
                "key_contact",
                "market_insight",
                "communication_pattern"
            ]
            if memory_type not in valid_types:
                print(f"[MEMORY] ⚠ Invalid memory type: {memory_type}")
                continue

            # Create memory entry
            memory = add_memory(
                ceo_id=ceo_id,
                memory_type=memory_type,
                content=content,
                source_session_id=session_id,
                confidence=confidence
            )

            if memory:
                created_memories.append(memory)

        print(f"[MEMORY] ✅ Created {len(created_memories)} memories")
        return created_memories

    except json.JSONDecodeError as e:
        print(f"[MEMORY] ✗ Failed to parse JSON: {e}")
        print(f"[MEMORY] Response was: {raw_response}")
        return []
    except Exception as e:
        print(f"[MEMORY] ✗ Error consolidating memory: {e}")
        return []


def generate_welcome_back(ceo_id: str, chat_id: int) -> Optional[str]:
    """
    Generate a warm welcome-back message that summarizes current state.

    Args:
        ceo_id: UUID of the CEO
        chat_id: Telegram chat ID

    Returns:
        Welcome message string or None
    """
    print("\n[MEMORY] Generating welcome back message...")

    # Step 1: Load CEO context
    ceo_context = get_ceo_context()
    if not ceo_context:
        print("[MEMORY] ✗ No CEO context found")
        return None

    ceo_name = ceo_context.get("name", "").split()[0]  # First name only

    # Step 2: Load memory profile and recent sessions
    memory_profile = get_memory_profile(ceo_id)
    recent_sessions = get_recent_sessions(ceo_id, limit=3)

    # Step 3: Check for active session
    active_session = get_active_session(chat_id)

    # Step 4: Build context for Gemini
    context_parts = []

    # Memory profile
    if memory_profile:
        context_parts.append("=== LONG-TERM MEMORY ===")
        for i, memory in enumerate(memory_profile[:10], 1):  # Show top 10
            mem_type = memory.get("memory_type", "").replace("_", " ").title()
            content = memory.get("content")
            context_parts.append(f"{i}. [{mem_type}] {content}")
        context_parts.append("")

    # Recent sessions
    if recent_sessions:
        context_parts.append("=== RECENT SESSIONS ===")
        for i, session in enumerate(recent_sessions, 1):
            state = session.get("state")
            created = session.get("started_at", "")[:10]  # Date only
            decisions = session.get("decisions", [])
            decision_summary = f"{len(decisions)} decision(s)" if decisions else "no decisions yet"
            context_parts.append(f"{i}. Session from {created} - {state} - {decision_summary}")
        context_parts.append("")

    # Active session
    if active_session:
        context_parts.append("=== ACTIVE SESSION ===")
        context_parts.append(f"State: {active_session.get('state')}")
        context_parts.append(f"Started: {active_session.get('started_at', '')[:16]}")
        context_parts.append("")

    context = "\n".join(context_parts)

    # Step 5: Create prompt for Gemini
    prompt = f"""You are generating a warm welcome-back message for {ceo_name}, a CEO who is returning to their AI assistant.

{context}

TASK:
Generate a concise, warm welcome message (2-3 sentences max) that:
1. Greets {ceo_name} warmly
2. Briefly summarizes where things stand (pending work, recent decisions, or current focus)
3. Asks what they'd like to work on: continue from where they left off OR start something new

TONE:
- Friendly and professional
- Brief and to the point
- Reference specific context when possible (but don't overwhelm)
- Natural, conversational style

EXAMPLES OF GOOD MESSAGES:
"Welcome back, {ceo_name}! 👋 You've been focused on Spain market expansion and institutional sales. Want to continue refining that strategy, or work on something new?"

"Hey {ceo_name}! Last time we were discussing pilot customer definitions and you decided to formalize success metrics. Ready to continue, or something else on your mind?"

"Hi {ceo_name}! 👋 We've been building your expansion roadmap. Should we keep going with that, or is there something new you'd like to tackle?"

RULES:
- Maximum 3 sentences
- Include one emoji (👋 or similar)
- Reference recent work if available
- Always end with the choice: continue or new topic
- Use {ceo_name}'s first name

OUTPUT:
Return ONLY the welcome message, nothing else.

WELCOME MESSAGE:"""

    print("[MEMORY] Calling Gemini to generate welcome message...")

    # Step 6: Call Gemini to generate message
    @retry_with_fallback(max_retries=MAX_RETRIES, wait_seconds=RETRY_WAIT_SECONDS)
    def call_gemini_with_retry():
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            print(f"[MEMORY] ✗ Gemini API call failed: {str(e)}")
            raise

    try:
        welcome_message = call_gemini_with_retry()
        print(f"[MEMORY] ✅ Generated welcome message: {welcome_message[:80]}...")
        return welcome_message

    except Exception as e:
        print(f"[MEMORY] ✗ Error generating welcome message: {e}")
        # Fallback message
        return f"Welcome back, {ceo_name}! 👋 What would you like to work on today?"


def should_send_welcome_back(chat_id: int) -> bool:
    """
    Determine if we should send a welcome-back message.

    Criteria: More than 2 hours since last message OR first message of the day.

    Args:
        chat_id: Telegram chat ID

    Returns:
        True if should send welcome back, False otherwise
    """
    last_message_time = get_last_message_time(chat_id)

    print(f"[MEMORY] Checking welcome back for chat {chat_id}...")
    print(f"[MEMORY] Last message time from DB: {last_message_time}")

    if not last_message_time:
        # No previous messages - this is the first ever
        print("[MEMORY] ✓ First message ever - sending welcome")
        return True

    try:
        # Parse the timestamp
        from datetime import datetime

        # Handle both with and without timezone
        if 'Z' in last_message_time or '+' in last_message_time:
            last_time = datetime.fromisoformat(last_message_time.replace('Z', '+00:00'))
        else:
            # Assume UTC if no timezone
            last_time = datetime.fromisoformat(last_message_time)
            from datetime import timezone
            last_time = last_time.replace(tzinfo=timezone.utc)

        now = datetime.now(last_time.tzinfo)

        time_diff = now - last_time
        hours_diff = time_diff.total_seconds() / 3600

        print(f"[MEMORY] Time difference: {hours_diff:.2f} hours")
        print(f"[MEMORY] Last: {last_time}, Now: {now}")

        # Check if more than 2 hours
        if hours_diff >= 2:
            print(f"[MEMORY] ✓ {hours_diff:.1f} hours since last message - sending welcome")
            return True

        # Check if different day
        if last_time.date() != now.date():
            print(f"[MEMORY] ✓ New day - sending welcome")
            return True

        print(f"[MEMORY] ✗ Recent activity ({hours_diff:.1f}h ago) - skipping welcome")
        return False

    except Exception as e:
        print(f"[MEMORY] ✗ Error checking message time: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Test the memory agent
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "consolidate":
        # Test consolidation
        test_session_id = input("Enter session ID to consolidate: ")
        test_ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"  # Alex's ID

        memories = consolidate_session_memory(test_session_id, test_ceo_id)
        print(f"\n✅ Created {len(memories)} memories:")
        for memory in memories:
            print(f"  - [{memory['memory_type']}] {memory['content']}")

    elif len(sys.argv) > 1 and sys.argv[1] == "welcome":
        # Test welcome message
        test_ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"
        test_chat_id = 8866294087

        message = generate_welcome_back(test_ceo_id, test_chat_id)
        print(f"\n✅ Welcome message:\n{message}")

    else:
        print("Usage:")
        print("  python memory_agent.py consolidate")
        print("  python memory_agent.py welcome")
