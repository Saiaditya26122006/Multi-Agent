"""
Test script to verify the 3-question limit works correctly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory.supabase_client import (
    get_active_session,
    get_assumptions_for_session,
    create_session,
    supabase
)
from agents.l1_clarity_agent import generate_clarifying_question

def test_question_limit():
    """Test that L1 stops after 3 questions"""

    print("\n" + "="*60)
    print("TEST: L1 Question Limit (Max 3 Questions)")
    print("="*60)

    # Get or create a test session
    chat_id = 8866294087
    ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"

    # Clean up - delete any existing test session assumptions
    print("\n[SETUP] Cleaning up previous test data...")
    session = get_active_session(chat_id)

    if session:
        session_id = session['id']
        print(f"[SETUP] Found active session: {session_id}")

        # Delete existing assumptions for this session
        result = supabase.table("assumptions").delete().eq("session_id", session_id).execute()
        print(f"[SETUP] Deleted {len(result.data)} existing assumptions")
    else:
        # Create new session
        session = create_session(ceo_id, chat_id)
        session_id = session['id']
        print(f"[SETUP] Created new session: {session_id}")

    # Test: Call L1 four times, should get questions 1, 2, 3, then clarification_complete
    test_messages = [
        "I want to grow my business",
        "I want to expand internationally",
        "I need to increase revenue",
        "I want to hire more people"
    ]

    for i, message in enumerate(test_messages, 1):
        print(f"\n{'='*60}")
        print(f"TEST ITERATION {i}")
        print(f"{'='*60}")
        print(f"Message: {message}")

        # Check current assumption count
        assumptions = get_assumptions_for_session(session_id)
        print(f"\nCurrent assumptions count: {len(assumptions)}")

        if i <= 3:
            print(f"\n[EXPECTED] Should generate question {i}/3")
        else:
            print(f"\n[EXPECTED] Should return clarification_complete=True (already asked 3)")

        # Call L1
        result = generate_clarifying_question(
            session_id=session_id,
            ceo_id=ceo_id,
            message_text=message
        )

        # Check result
        print(f"\n[RESULT] clarification_complete: {result.get('clarification_complete')}")

        if result.get("clarification_complete"):
            print("[RESULT] ✓ L1 correctly returned clarification_complete=True")
            print("[RESULT] ✓ No question generated (as expected)")

            if i <= 3:
                print(f"\n[ERROR] ✗ Should have generated question {i}/3, not stopped early!")
                return False
            else:
                print(f"\n[SUCCESS] ✓ Correctly stopped after 3 questions!")
                break
        else:
            question = result.get("question")
            assumption_id = result.get("assumption_id")
            print(f"[RESULT] ✓ Generated question: {question[:80]}...")
            print(f"[RESULT] ✓ Created assumption: {assumption_id}")

            if i > 3:
                print(f"\n[ERROR] ✗ Should have stopped after 3 questions, but generated question 4!")
                return False

    # Verify final state
    print(f"\n{'='*60}")
    print("FINAL VERIFICATION")
    print(f"{'='*60}")

    final_assumptions = get_assumptions_for_session(session_id)
    print(f"Total assumptions created: {len(final_assumptions)}")

    if len(final_assumptions) == 3:
        print("[SUCCESS] ✅ Exactly 3 assumptions created (3 questions asked)")
        print("[SUCCESS] ✅ Question limit working correctly!")
        return True
    else:
        print(f"[ERROR] ✗ Expected 3 assumptions, got {len(final_assumptions)}")
        return False

if __name__ == "__main__":
    try:
        success = test_question_limit()

        print("\n" + "="*60)
        if success:
            print("TEST PASSED ✅")
        else:
            print("TEST FAILED ✗")
        print("="*60 + "\n")

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
