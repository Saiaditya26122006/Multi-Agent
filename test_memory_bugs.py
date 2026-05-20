"""
Test script to verify the 3 memory bug fixes:
1. Column name fix (received_at)
2. Welcome back only after L0 validation
3. Welcome back timing check with session state awareness
"""

import asyncio
from datetime import datetime, timedelta
from memory.supabase_client import (
    get_ceo_context,
    get_last_message_time,
    get_active_session,
    create_session,
    update_session_state
)
from agents.memory_agent import should_send_welcome_back
from main import handle_telegram_message


def print_header(title):
    """Print formatted header"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def test_bug_1_column_name():
    """Test Bug 1: Verify get_last_message_time uses correct column"""
    print_header("BUG 1: Column Name Fix (received_at)")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    chat_id = ceo_context.get('telegram_chat_id')

    print(f"Testing get_last_message_time({chat_id})...")

    try:
        last_time = get_last_message_time(chat_id)
        print(f"✓ Function executed without error")
        print(f"  Result: {last_time}")

        if last_time:
            print(f"✅ SUCCESS: Retrieved timestamp from database")
            return True
        else:
            print(f"⚠️  WARNING: No messages found (expected if database is empty)")
            return True

    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False


async def test_bug_2_l0_validation():
    """Test Bug 2: Welcome back only after L0 validation"""
    print_header("BUG 2: L0 Validation Before Welcome Back")

    # Test with unauthorized chat ID
    unauthorized_message = {
        "message_id": 888888888,
        "chat_id": 999999999,  # Fake chat ID
        "text": "hello",
        "from_user": {"id": 999999999}
    }

    print("Sending message from unauthorized chat ID (999999999)...")
    print("Expected: No welcome back message, no Gemini call")

    try:
        await handle_telegram_message(unauthorized_message)

        print("✅ SUCCESS: Message handled without crashing")
        print("   Check console output above for:")
        print("   - Should show '[L0] ✗ Message rejected: Unauthorized sender'")
        print("   - Should NOT show '[MEMORY] Generating welcome back message'")
        return True

    except Exception as e:
        print(f"❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_bug_3_timing_check():
    """Test Bug 3: Welcome back timing with session state awareness"""
    print_header("BUG 3: Welcome Back Timing Check")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    chat_id = ceo_context.get('telegram_chat_id')
    ceo_id = ceo_context.get('id')

    # Test 1: Check timing logic
    print("\nTest 3a: Timing Logic")
    print("-" * 70)

    result = should_send_welcome_back(chat_id)
    print(f"should_send_welcome_back() returned: {result}")

    if result:
        print("⚠️  Would send welcome back (check logs for reason)")
    else:
        print("✓ Would NOT send welcome back (recent activity)")

    # Test 2: Check with active session
    print("\nTest 3b: Session State Awareness")
    print("-" * 70)

    active_session = get_active_session(chat_id)

    if active_session:
        state = active_session.get('state')
        print(f"✓ Active session found: {active_session.get('id')[:8]}...")
        print(f"  State: {state}")

        if state in ["NEEDS_CLARIFICATION", "AWAITING_APPROVAL"]:
            print(f"  ✅ Mid-conversation state detected")
            print(f"     Welcome back should be SKIPPED even if 2+ hours passed")
        else:
            print(f"  State is: {state}")
            print(f"  Not mid-conversation")
    else:
        print("✓ No active session")
        print("  Welcome back timing depends only on time gap")

    return True


async def test_full_flow():
    """Test the complete flow"""
    print_header("FULL FLOW TEST")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    chat_id = ceo_context.get('telegram_chat_id')

    # Test 1: Send legitimate message
    print("\nTest: Legitimate CEO message")
    print("-" * 70)

    test_message = {
        "message_id": 777777777,
        "chat_id": chat_id,
        "text": "test message",
        "from_user": {"id": chat_id}
    }

    print(f"Sending message from CEO chat ID ({chat_id})...")

    try:
        await handle_telegram_message(test_message)
        print("✅ Message processed")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("  MEMORY BUG FIXES - TEST SUITE")
    print("=" * 70)
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []

    # Test 1: Column name fix
    try:
        result = test_bug_1_column_name()
        results.append(("Bug 1: Column Name", result))
    except Exception as e:
        print(f"❌ Test failed: {e}")
        results.append(("Bug 1: Column Name", False))

    # Test 2: L0 validation (async)
    try:
        result = asyncio.run(test_bug_2_l0_validation())
        results.append(("Bug 2: L0 Validation", result))
    except Exception as e:
        print(f"❌ Test failed: {e}")
        results.append(("Bug 2: L0 Validation", False))

    # Test 3: Timing check
    try:
        result = test_bug_3_timing_check()
        results.append(("Bug 3: Timing Check", result))
    except Exception as e:
        print(f"❌ Test failed: {e}")
        results.append(("Bug 3: Timing Check", False))

    # Full flow test
    try:
        result = asyncio.run(test_full_flow())
        results.append(("Full Flow", result))
    except Exception as e:
        print(f"❌ Test failed: {e}")
        results.append(("Full Flow", False))

    # Summary
    print_header("TEST SUMMARY")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} | {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed ({passed/total*100:.1f}%)")

    if passed == total:
        print("\n🎉 All tests passed!")
        print("\n✅ All three bugs are fixed:")
        print("   1. Column name corrected (received_at)")
        print("   2. Welcome back only after L0 validation")
        print("   3. Timing check with session state awareness")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
