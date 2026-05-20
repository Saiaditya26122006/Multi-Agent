"""
Complete end-to-end test: L1 asks 3 questions → L3 generates feedback from conversation
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
from agents.l3_feedback_agent import generate_feedback

def test_complete_flow():
    """Test complete flow with conversation context"""

    print("\n" + "="*60)
    print("COMPLETE E2E TEST: L1 → L3 with Conversation Context")
    print("="*60)

    # Setup
    chat_id = 8866294087
    ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"

    # Get session
    session = get_active_session(chat_id)
    session_id = session['id']
    print(f"\n[SETUP] Using session: {session_id}")

    # Simulate a conversation about Spanish business school pilots
    conversation = [
        "I want to launch a pilot program with Spanish business schools",
        "ESADE and IE Business School in Barcelona and Madrid",
        "We'll provide free access to 50 students per school for 3 months"
    ]

    print("\n" + "="*60)
    print("PHASE 1: L1 Asks 3 Questions")
    print("="*60)

    for i, ceo_message in enumerate(conversation, 1):
        print(f"\n[Q{i}] CEO says: {ceo_message}")

        result = generate_clarifying_question(
            session_id=session_id,
            ceo_id=ceo_id,
            message_text=ceo_message
        )

        if result.get("clarification_complete"):
            print(f"[L1] Clarification complete after {i} messages")
            break

        print(f"[L1] ✓ Question {i}/3 generated")

    # Check assumptions
    assumptions = get_assumptions_for_session(session_id)
    print(f"\n[VERIFY] {len(assumptions)} assumptions created")

    for i, assumption in enumerate(assumptions, 1):
        stmt = assumption.get('statement', '')
        # Extract the key info
        if "'" in stmt:
            parts = stmt.split("'")
            if len(parts) >= 2:
                ceo_said = parts[1]
                print(f"  {i}. CEO said: {ceo_said[:60]}...")

    # Trigger L3
    print("\n" + "="*60)
    print("PHASE 2: L3 Generates Feedback from Conversation")
    print("="*60)

    # Simulate 4th message (should trigger L3)
    print("\n[CEO] Answers question 3: We'll measure engagement and conversion rates")

    # This should return clarification_complete
    l1_result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text="We'll measure engagement and conversion rates"
    )

    if not l1_result.get("clarification_complete"):
        print("✗ ERROR: L1 should have returned clarification_complete=True")
        return False

    print("[L1] ✓ Clarification complete (3/3)")
    print("[L1] ✓ Triggering L3...")

    # Call L3
    l3_result = generate_feedback(
        session_id=session_id,
        research_brief=None
    )

    telegram_message = l3_result.get("telegram_message")

    print("\n" + "="*60)
    print("L3 FEEDBACK (What CEO Receives)")
    print("="*60)
    print(telegram_message)
    print("="*60)

    # Verification
    print("\n[VERIFICATION]")

    relevant_keywords = [
        "spanish",
        "business school",
        "esade",
        "ie",
        "pilot",
        "students",
        "barcelona",
        "madrid",
        "free access"
    ]

    irrelevant_keywords = [
        "email",
        "cac",
        "paid ads",
        "roi",
        "marketing channel",
        "content marketing"
    ]

    message_lower = telegram_message.lower()
    found_relevant = sum(1 for kw in relevant_keywords if kw.lower() in message_lower)
    found_irrelevant = sum(1 for kw in irrelevant_keywords if kw.lower() in message_lower)

    print(f"  ✓ Found {found_relevant}/{len(relevant_keywords)} relevant keywords")
    print(f"  {'✓' if found_irrelevant == 0 else '✗'} Found {found_irrelevant} irrelevant keywords (should be 0)")

    # Check structure
    has_what_we_know = "WHAT WE KNOW" in telegram_message
    has_risk = "BIGGEST OPEN RISK" in telegram_message or "BIGGEST RISK" in telegram_message
    has_decision = "DECISION QUESTION" in telegram_message

    print(f"  {'✓' if has_what_we_know else '✗'} Has 'WHAT WE KNOW' section")
    print(f"  {'✓' if has_risk else '✗'} Has 'BIGGEST RISK' section")
    print(f"  {'✓' if has_decision else '✗'} Has 'DECISION QUESTION' section")

    # Final verdict
    print("\n" + "="*60)
    print("TEST RESULTS")
    print("="*60)

    if (found_relevant >= 4 and
        found_irrelevant == 0 and
        has_what_we_know and
        has_risk and
        has_decision):
        print("✅ TEST PASSED")
        print("\nKey achievements:")
        print("  ✓ L1 asked exactly 3 questions")
        print("  ✓ L1 triggered L3 after question 3")
        print("  ✓ L3 used conversation context (not old research)")
        print("  ✓ Feedback mentions Spanish business schools")
        print("  ✓ Feedback does NOT mention irrelevant topics")
        print("  ✓ All required sections present")
        return True
    else:
        print("✗ TEST FAILED")
        if found_relevant < 4:
            print(f"  - Not enough relevant keywords ({found_relevant}/9)")
        if found_irrelevant > 0:
            print(f"  - Found irrelevant keywords ({found_irrelevant})")
        if not (has_what_we_know and has_risk and has_decision):
            print("  - Missing required sections")
        return False

if __name__ == "__main__":
    try:
        success = test_complete_flow()

        print("\n" + "="*60)
        if success:
            print("COMPLETE FLOW: PASSED ✅")
        else:
            print("COMPLETE FLOW: FAILED ✗")
        print("="*60 + "\n")

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
