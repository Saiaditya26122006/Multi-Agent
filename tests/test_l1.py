"""
Test suite for L1 Clarity Agent
"""

import sys
from pathlib import Path
import random

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.l1_clarity_agent import generate_clarifying_question
from memory.supabase_client import (
    get_ceo_context,
    get_active_session,
    create_session,
    supabase
)


def test_vague_message_produces_question():
    """
    Test 1: A vague CEO message produces one focused question
    """
    print("\n" + "=" * 60)
    print("TEST 1: Vague Message Produces Focused Question")
    print("=" * 60)

    # Get CEO context
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ FAIL: No CEO context found")
        return False

    ceo_id = ceo_context.get("id")
    ceo_chat_id = ceo_context.get("telegram_chat_id", 8866294087)

    # Get or create a session
    session = get_active_session(ceo_chat_id)
    if not session:
        session = create_session(ceo_id, ceo_chat_id)

    if not session:
        print("✗ FAIL: Could not get or create session")
        return False

    session_id = session.get("id")
    print(f"Using session: {session_id}")

    # Test with a vague message
    vague_message = "I want to grow the business"

    print(f"\nCEO Message: '{vague_message}'")
    print("Generating clarifying question...")

    result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text=vague_message
    )

    print("\nResult:")
    print(f"  Question: {result['question']}")
    print(f"  Assumption ID: {result['assumption_id']}")
    print(f"  Session ID: {result['session_id']}")

    # Assertions
    assert "question" in result, "Result should contain 'question'"
    assert result["question"], "Question should not be empty"
    assert isinstance(result["question"], str), "Question should be a string"
    assert len(result["question"]) > 10, "Question should be substantial (>10 chars)"
    assert "?" in result["question"], "Question should contain a question mark"

    print("\n✓ TEST 1 PASSED")
    return True


def test_assumption_written_to_supabase():
    """
    Test 2: The assumption is written to Supabase
    """
    print("\n" + "=" * 60)
    print("TEST 2: Assumption Written to Supabase")
    print("=" * 60)

    # Get CEO context and session
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ FAIL: No CEO context found")
        return False

    ceo_id = ceo_context.get("id")
    ceo_chat_id = ceo_context.get("telegram_chat_id", 8866294087)

    session = get_active_session(ceo_chat_id)
    if not session:
        session = create_session(ceo_id, ceo_chat_id)

    session_id = session.get("id")

    # Generate a question (which should create an assumption)
    test_message = "I need help with marketing"

    print(f"CEO Message: '{test_message}'")
    print("Generating question and creating assumption...")

    result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text=test_message
    )

    assumption_id = result["assumption_id"]
    print(f"\nAssumption ID: {assumption_id}")

    # Verify the assumption exists in Supabase
    print("Verifying assumption in database...")

    try:
        response = (
            supabase.table("assumptions")
            .select("*")
            .eq("assumption_id", assumption_id)
            .execute()
        )

        if not response.data or len(response.data) == 0:
            print("✗ FAIL: Assumption not found in database")
            return False

        assumption = response.data[0]
        print("\nAssumption found:")
        print(f"  Statement: {assumption.get('statement')[:80]}...")
        print(f"  Confidence: {assumption.get('confidence')}")
        print(f"  Clarification Status: {assumption.get('clarification_status')}")
        print(f"  Session ID: {assumption.get('session_id')}")

        # Assertions
        assert assumption.get("confidence") == "low", "Confidence should be 'low'"
        assert assumption.get("clarification_status") == "pending", "Status should be 'pending'"
        assert assumption.get("session_id") == session_id, "Session ID should match"
        assert assumption.get("status") == "active", "Assumption should be active"

        print("\n✓ TEST 2 PASSED")
        return True

    except Exception as e:
        print(f"✗ FAIL: Error verifying assumption: {e}")
        return False


def test_session_state_updated():
    """
    Test 3: Session state is updated to NEEDS_CLARIFICATION
    """
    print("\n" + "=" * 60)
    print("TEST 3: Session State Updated to NEEDS_CLARIFICATION")
    print("=" * 60)

    # Get CEO context and session
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ FAIL: No CEO context found")
        return False

    ceo_id = ceo_context.get("id")
    ceo_chat_id = ceo_context.get("telegram_chat_id", 8866294087)

    session = get_active_session(ceo_chat_id)
    if not session:
        session = create_session(ceo_id, ceo_chat_id)

    session_id = session.get("id")
    print(f"Session ID: {session_id}")
    print(f"Initial state: {session.get('state')}")

    # Generate a question (which should update the session state)
    test_message = "What should I do next?"

    print(f"\nCEO Message: '{test_message}'")
    print("Generating question...")

    result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text=test_message
    )

    # Verify the session state is updated
    print("\nVerifying session state in database...")

    try:
        response = (
            supabase.table("sessions")
            .select("*")
            .eq("id", session_id)
            .execute()
        )

        if not response.data or len(response.data) == 0:
            print("✗ FAIL: Session not found in database")
            return False

        session = response.data[0]
        current_state = session.get("state")

        print(f"Current state: {current_state}")

        # Assertion
        assert current_state == "NEEDS_CLARIFICATION", f"State should be 'NEEDS_CLARIFICATION', got '{current_state}'"

        print("\n✓ TEST 3 PASSED")
        return True

    except Exception as e:
        print(f"✗ FAIL: Error verifying session state: {e}")
        return False


def run_all_tests():
    """Run all L1 tests"""
    print("\n" + "=" * 60)
    print("L1 CLARITY AGENT - TEST SUITE")
    print("=" * 60)

    tests = [
        ("Vague Message Produces Question", test_vague_message_produces_question),
        ("Assumption Written to Supabase", test_assumption_written_to_supabase),
        ("Session State Updated", test_session_state_updated)
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except AssertionError as e:
            print(f"\n✗ TEST FAILED: {test_name}")
            print(f"  Error: {e}")
            results.append((test_name, False))
        except Exception as e:
            print(f"\n✗ TEST ERROR: {test_name}")
            print(f"  Exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"  {status}: {test_name}")

    print("=" * 60)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    if not success:
        exit(1)
