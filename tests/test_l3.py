"""
Test suite for L3 Feedback Agent
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.l3_feedback_agent import generate_feedback
from memory.supabase_client import (
    get_ceo_context,
    get_active_session,
    create_session,
    supabase
)


def test_research_brief_produces_summary():
    """
    Test 1: A research brief produces a clean summary
    """
    print("\n" + "=" * 60)
    print("TEST 1: Research Brief Produces Clean Summary")
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
    print(f"Using session: {session_id}")

    # Create a test research brief
    test_brief = {
        "research_id": "test_research_l3_001",
        "topic": "Customer retention strategies for Q3",
        "source_type": "system_structured",
        "key_findings": [
            "Current churn rate is 8% monthly",
            "Top 3 reasons for churn: pricing, onboarding, support",
            "Competitors offering 30-day free trials"
        ],
        "evidence_quality": "high",
        "remaining_uncertainty": "Impact of price changes on existing customers",
        "decision_relevance": "Must decide on retention strategy by end of month",
        "session_id": session_id
    }

    # Insert test research brief
    try:
        supabase.table("research_briefs").delete().eq("research_id", "test_research_l3_001").execute()
        supabase.table("research_briefs").insert(test_brief).execute()
        print("✓ Test research brief created")
    except Exception as e:
        print(f"Error creating test brief: {e}")
        return False

    # Generate feedback
    print("\nGenerating feedback...")

    result = generate_feedback(
        session_id=session_id,
        research_brief=test_brief
    )

    print("\nResult:")
    print(f"  Summary length: {len(result['summary'])} chars")
    print(f"  Decision ID: {result['decision_id']}")
    print(f"  Telegram message length: {len(result['telegram_message'])} chars")

    # Assertions
    assert "summary" in result, "Result should contain 'summary'"
    assert result["summary"], "Summary should not be empty"
    assert isinstance(result["summary"], str), "Summary should be a string"
    assert len(result["summary"]) > 50, "Summary should be substantial (>50 chars)"

    assert "telegram_message" in result, "Result should contain 'telegram_message'"
    assert result["telegram_message"], "Telegram message should not be empty"

    # Check that markdown is cleaned up
    assert "**" not in result["telegram_message"], "Telegram message should not contain ** markdown"
    assert "##" not in result["telegram_message"], "Telegram message should not contain ## markdown"

    print("\nSummary preview:")
    print(result['summary'][:200] + "...")

    print("\n✓ TEST 1 PASSED")
    return True


def test_decision_created_in_supabase():
    """
    Test 2: A decision object is created in Supabase
    """
    print("\n" + "=" * 60)
    print("TEST 2: Decision Created in Supabase")
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

    # Create test research brief
    test_brief = {
        "research_id": "test_research_l3_002",
        "topic": "New feature prioritization",
        "source_type": "system_structured",
        "key_findings": [
            "Feature A requested by 60% of users",
            "Feature B has 3x higher revenue potential",
            "Engineering capacity allows one feature this quarter"
        ],
        "evidence_quality": "high",
        "remaining_uncertainty": "User adoption rate for Feature B",
        "decision_relevance": "Must prioritize roadmap for Q3",
        "session_id": session_id
    }

    # Insert test research brief
    try:
        supabase.table("research_briefs").delete().eq("research_id", "test_research_l3_002").execute()
        supabase.table("research_briefs").insert(test_brief).execute()
        print("✓ Test research brief created")
    except Exception as e:
        print(f"Error creating test brief: {e}")
        return False

    # Generate feedback (which should create a decision)
    print("\nGenerating feedback...")

    result = generate_feedback(
        session_id=session_id,
        research_brief=test_brief
    )

    decision_id = result["decision_id"]
    print(f"\nDecision ID: {decision_id}")

    # Verify the decision exists in Supabase
    print("Verifying decision in database...")

    try:
        response = (
            supabase.table("decisions")
            .select("*")
            .eq("decision_id", decision_id)
            .execute()
        )

        if not response.data or len(response.data) == 0:
            print("✗ FAIL: Decision not found in database")
            return False

        decision = response.data[0]
        print("\nDecision found:")
        print(f"  Decision: {decision.get('decision')[:80]}...")
        print(f"  Rationale: {decision.get('rationale')[:80]}...")
        print(f"  Status: {decision.get('status')}")
        print(f"  Session ID: {decision.get('session_id')}")

        # Assertions
        assert decision.get("status") == "pending_approval", "Status should be 'pending_approval'"
        assert decision.get("session_id") == session_id, "Session ID should match"
        assert decision.get("rationale"), "Rationale should not be empty"
        assert decision.get("decision"), "Decision should not be empty"

        print("\n✓ TEST 2 PASSED")
        return True

    except Exception as e:
        print(f"✗ FAIL: Error verifying decision: {e}")
        return False


def test_session_state_updated_to_awaiting_approval():
    """
    Test 3: Session state updates to AWAITING_APPROVAL
    """
    print("\n" + "=" * 60)
    print("TEST 3: Session State Updated to AWAITING_APPROVAL")
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

    # Create test research brief
    test_brief = {
        "research_id": "test_research_l3_003",
        "topic": "Marketing budget allocation",
        "source_type": "system_structured",
        "key_findings": [
            "Current CAC is $150 per customer",
            "Email campaigns have 2x ROI vs paid ads",
            "Content marketing shows 6-month lag to conversion"
        ],
        "evidence_quality": "medium",
        "remaining_uncertainty": "Competitor response to increased spending",
        "decision_relevance": "Budget decisions due next week",
        "session_id": session_id
    }

    # Insert test research brief
    try:
        supabase.table("research_briefs").delete().eq("research_id", "test_research_l3_003").execute()
        supabase.table("research_briefs").insert(test_brief).execute()
        print("✓ Test research brief created")
    except Exception as e:
        print(f"Error creating test brief: {e}")
        return False

    # Generate feedback (which should update session state)
    print("\nGenerating feedback...")

    result = generate_feedback(
        session_id=session_id,
        research_brief=test_brief
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
        assert current_state == "AWAITING_APPROVAL", f"State should be 'AWAITING_APPROVAL', got '{current_state}'"

        print("\n✓ TEST 3 PASSED")
        return True

    except Exception as e:
        print(f"✗ FAIL: Error verifying session state: {e}")
        return False


def run_all_tests():
    """Run all L3 tests"""
    print("\n" + "=" * 60)
    print("L3 FEEDBACK AGENT - TEST SUITE")
    print("=" * 60)

    tests = [
        ("Research Brief Produces Summary", test_research_brief_produces_summary),
        ("Decision Created in Supabase", test_decision_created_in_supabase),
        ("Session State Updated", test_session_state_updated_to_awaiting_approval)
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
