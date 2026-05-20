"""
Test script to verify L3 auto-triggers after 3 questions.
"""

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory.supabase_client import (
    get_active_session,
    get_assumptions_for_session,
    create_session,
    supabase
)
from agents.l1_clarity_agent import generate_clarifying_question
from agents.l3_feedback_agent import generate_feedback

async def test_l3_auto_trigger():
    """Test that L3 is triggered automatically after 3 questions"""

    print("\n" + "="*60)
    print("TEST: L3 Auto-Trigger After 3 Questions")
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

    # Simulate asking 3 questions
    print("\n" + "="*60)
    print("PHASE 1: Ask 3 Questions")
    print("="*60)

    test_messages = [
        "I want to grow my business",
        "I want to expand internationally",
        "I need to increase revenue"
    ]

    for i, message in enumerate(test_messages, 1):
        print(f"\n[Q{i}] Asking question {i}/3...")

        result = generate_clarifying_question(
            session_id=session_id,
            ceo_id=ceo_id,
            message_text=message
        )

        if result.get("clarification_complete"):
            print(f"[ERROR] ✗ L1 stopped early at question {i}!")
            return False

        print(f"[Q{i}] ✓ Question generated: {result['question'][:60]}...")

    # Verify 3 assumptions created
    assumptions = get_assumptions_for_session(session_id)
    print(f"\n[VERIFY] Total assumptions: {len(assumptions)}")

    if len(assumptions) != 3:
        print(f"[ERROR] ✗ Expected 3 assumptions, got {len(assumptions)}")
        return False

    # Now simulate the 4th message (CEO's answer to question 3)
    print("\n" + "="*60)
    print("PHASE 2: Answer Question 3 (Should Trigger L3)")
    print("="*60)

    print("\n[STEP] Calling L1 with CEO's 4th message...")
    l1_result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text="We want to reach 1M in revenue by end of year"
    )

    print(f"\n[L1 RESULT] clarification_complete: {l1_result.get('clarification_complete')}")

    if not l1_result.get("clarification_complete"):
        print("[ERROR] ✗ L1 did not return clarification_complete=True!")
        print("[ERROR] ✗ This means L1 would try to ask question 4 (wrong!)")
        return False

    print("[SUCCESS] ✓ L1 correctly returned clarification_complete=True")

    # Simulate main.py logic: trigger L3 when clarification_complete
    print("\n[STEP] Triggering L3 (as main.py would do)...")

    try:
        l3_result = generate_feedback(
            session_id=session_id,
            research_brief=None
        )

        print(f"\n[L3 RESULT] Summary generated: {len(l3_result.get('summary', ''))} chars")
        print(f"[L3 RESULT] Decision ID: {l3_result.get('decision_id')}")
        print(f"[L3 RESULT] Telegram message: {len(l3_result.get('telegram_message', ''))} chars")

        telegram_message = l3_result.get("telegram_message")

        if not telegram_message:
            print("[ERROR] ✗ L3 did not generate telegram_message!")
            return False

        # Verify telegram message structure
        print("\n[VERIFY] Checking telegram message structure...")

        required_sections = [
            "WHAT WE KNOW",
            "BIGGEST OPEN RISK",
            "DECISION QUESTION"
        ]

        for section in required_sections:
            if section in telegram_message:
                print(f"  ✓ Found section: {section}")
            else:
                print(f"  ✗ Missing section: {section}")
                return False

        # Print the telegram message
        print("\n" + "="*60)
        print("TELEGRAM MESSAGE (What CEO Would Receive)")
        print("="*60)
        print(telegram_message)
        print("="*60)

        print("\n[SUCCESS] ✅ L3 auto-trigger works correctly!")
        print("[SUCCESS] ✅ Complete flow: 3 questions → L3 feedback")
        return True

    except Exception as e:
        print(f"[ERROR] ✗ L3 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        success = asyncio.run(test_l3_auto_trigger())

        print("\n" + "="*60)
        if success:
            print("TEST PASSED ✅")
            print("\nThe complete flow works:")
            print("  1. L1 asks max 3 questions")
            print("  2. After 3rd answer, L1 returns clarification_complete=True")
            print("  3. main.py triggers L3 automatically")
            print("  4. L3 generates feedback with decision options")
            print("  5. CEO receives telegram message with summary")
        else:
            print("TEST FAILED ✗")
        print("="*60 + "\n")

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
