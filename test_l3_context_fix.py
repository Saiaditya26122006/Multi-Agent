"""
Test script to verify L3 uses conversation context (not old research briefs).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory.supabase_client import (
    get_active_session,
    get_assumptions_for_session,
    create_session,
    create_assumption,
    supabase
)
from agents.l3_feedback_agent import generate_feedback

def test_l3_context():
    """Test that L3 generates feedback from conversation assumptions"""

    print("\n" + "="*60)
    print("TEST: L3 Uses Conversation Context (Not Old Research)")
    print("="*60)

    # Setup
    chat_id = 8866294087
    ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"

    # Clean up previous test data
    print("\n[SETUP] Cleaning up previous test data...")
    session = get_active_session(chat_id)

    if session:
        session_id = session['id']
        print(f"[SETUP] Found active session: {session_id}")

        # Delete existing assumptions
        result = supabase.table("assumptions").delete().eq("session_id", session_id).execute()
        print(f"[SETUP] Deleted {len(result.data)} existing assumptions")

        # Delete existing decisions
        result = supabase.table("decisions").delete().eq("session_id", session_id).execute()
        print(f"[SETUP] Deleted {len(result.data)} existing decisions")
    else:
        # Create new session
        session = create_session(ceo_id, chat_id)
        session_id = session['id']
        print(f"[SETUP] Created new session: {session_id}")

    # Create 3 test assumptions that represent a conversation about pilot contracts
    print("\n[SETUP] Creating test conversation about pilot contracts...")

    assumptions = [
        {
            "assumption_id": "assumption_test_001",
            "statement": "Assuming the CEO's message 'I want to launch a pilot program with Spanish business schools' requires clarification about: which specific business schools are you targeting?",
            "session_id": session_id,
            "confidence": "low",
            "clarification_status": "pending"
        },
        {
            "assumption_id": "assumption_test_002",
            "statement": "Assuming the CEO's answer 'ESADE and IE Business School in Barcelona and Madrid' requires clarification about: what is the scope of the pilot program?",
            "session_id": session_id,
            "confidence": "low",
            "clarification_status": "pending"
        },
        {
            "assumption_id": "assumption_test_003",
            "statement": "Assuming the CEO's answer 'We want to provide free access to 50 students per school for 3 months' requires clarification about: what metrics will you use to measure success?",
            "session_id": session_id,
            "confidence": "low",
            "clarification_status": "pending"
        }
    ]

    for assumption in assumptions:
        create_assumption(
            assumption_id=assumption["assumption_id"],
            statement=assumption["statement"],
            session_id=session_id,
            confidence=assumption["confidence"],
            clarification_status=assumption["clarification_status"]
        )
        print(f"[SETUP] ✓ Created: {assumption['assumption_id']}")

    # Verify assumptions were created
    loaded_assumptions = get_assumptions_for_session(session_id)
    print(f"\n[VERIFY] Loaded {len(loaded_assumptions)} assumptions")

    # Now call L3 to generate feedback
    print("\n" + "="*60)
    print("CALLING L3 FEEDBACK AGENT")
    print("="*60)

    try:
        result = generate_feedback(
            session_id=session_id,
            research_brief=None  # No research brief - should use assumptions
        )

        telegram_message = result.get("telegram_message")

        print("\n" + "="*60)
        print("L3 GENERATED FEEDBACK")
        print("="*60)
        print(telegram_message)
        print("="*60)

        # Verify the feedback mentions the actual conversation context
        print("\n[VERIFICATION]")

        keywords_that_should_appear = [
            "spanish",
            "business school",
            "esade",
            "ie business school",
            "pilot",
            "students",
            "barcelona",
            "madrid"
        ]

        keywords_that_should_not_appear = [
            "email campaign",
            "cac",
            "paid ads",
            "roi",
            "marketing channel"
        ]

        found_relevant = []
        found_irrelevant = []

        message_lower = telegram_message.lower()

        for keyword in keywords_that_should_appear:
            if keyword.lower() in message_lower:
                found_relevant.append(keyword)
                print(f"  ✓ Found relevant keyword: '{keyword}'")

        for keyword in keywords_that_should_not_appear:
            if keyword.lower() in message_lower:
                found_irrelevant.append(keyword)
                print(f"  ✗ Found IRRELEVANT keyword: '{keyword}' (should NOT appear!)")

        print("\n" + "="*60)
        print("TEST RESULTS")
        print("="*60)

        if len(found_relevant) >= 3 and len(found_irrelevant) == 0:
            print("✅ TEST PASSED")
            print(f"  - Found {len(found_relevant)} relevant keywords from conversation")
            print(f"  - Found 0 irrelevant keywords from old research")
            print("  - L3 is using conversation context correctly!")
            return True
        else:
            print("✗ TEST FAILED")
            print(f"  - Found {len(found_relevant)} relevant keywords (expected at least 3)")
            print(f"  - Found {len(found_irrelevant)} irrelevant keywords (expected 0)")
            if found_irrelevant:
                print(f"  - Irrelevant keywords: {', '.join(found_irrelevant)}")
            print("  - L3 is NOT using conversation context correctly!")
            return False

    except Exception as e:
        print(f"\n✗ TEST FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        success = test_l3_context()

        print("\n" + "="*60)
        if success:
            print("OVERALL: TEST PASSED ✅")
        else:
            print("OVERALL: TEST FAILED ✗")
        print("="*60 + "\n")

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
