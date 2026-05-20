"""
Test script for the persistent memory system
Tests memory consolidation, welcome back messages, and memory-aware agents
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from agents.memory_agent import consolidate_session_memory, generate_welcome_back, should_send_welcome_back
from memory.supabase_client import (
    get_memory_profile,
    get_recent_sessions,
    get_ceo_context,
    get_active_session
)


def print_header(title):
    """Print a formatted header"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def test_memory_profile():
    """Test 1: View current memory profile"""
    print_header("TEST 1: Current Memory Profile")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    ceo_id = ceo_context.get('id')
    memories = get_memory_profile(ceo_id)

    print(f"✓ Found {len(memories)} memory entries")

    if memories:
        print("\nMemory Entries:")
        for i, memory in enumerate(memories[:10], 1):
            mem_type = memory.get('memory_type', '').replace('_', ' ').title()
            content = memory.get('content')
            confidence = memory.get('confidence')
            created = memory.get('created_at', '')[:10]
            print(f"\n{i}. [{mem_type}] - Confidence: {confidence}")
            print(f"   {content}")
            print(f"   Created: {created}")
    else:
        print("\n(No memories yet - complete a session first)")

    return True


def test_recent_sessions():
    """Test 2: View recent sessions"""
    print_header("TEST 2: Recent Sessions")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    ceo_id = ceo_context.get('id')
    sessions = get_recent_sessions(ceo_id, limit=3)

    print(f"✓ Found {len(sessions)} recent sessions")

    if sessions:
        for i, session in enumerate(sessions, 1):
            state = session.get('state')
            created = session.get('created_at', '')[:16]
            session_id = session.get('id')
            decisions = session.get('decisions', [])
            assumptions = session.get('assumptions', [])

            print(f"\n{i}. Session {session_id[:8]}...")
            print(f"   Created: {created}")
            print(f"   State: {state}")
            print(f"   Decisions: {len(decisions)}")
            print(f"   Assumptions: {len(assumptions)}")
    else:
        print("\n(No sessions yet)")

    return True


def test_consolidate_memory():
    """Test 3: Consolidate memory from a session"""
    print_header("TEST 3: Memory Consolidation")

    print("This test requires a completed session.")
    session_id = input("Enter session ID to consolidate (or press Enter to skip): ").strip()

    if not session_id:
        print("⊘ Skipped")
        return True

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    ceo_id = ceo_context.get('id')

    print(f"\nConsolidating memory for session {session_id}...")
    memories = consolidate_session_memory(session_id, ceo_id)

    print(f"\n✓ Created {len(memories)} new memories:")
    for memory in memories:
        mem_type = memory.get('memory_type', '').replace('_', ' ').title()
        content = memory.get('content')
        print(f"  [{mem_type}] {content}")

    return True


def test_welcome_back():
    """Test 4: Generate welcome back message"""
    print_header("TEST 4: Welcome Back Message")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    ceo_id = ceo_context.get('id')
    chat_id = ceo_context.get('telegram_chat_id')

    # Check if should send welcome
    should_send = should_send_welcome_back(chat_id)
    print(f"Should send welcome back? {should_send}")

    # Generate message
    print("\nGenerating welcome back message...")
    message = generate_welcome_back(ceo_id, chat_id)

    if message:
        print("\n✓ Welcome message generated:")
        print("─" * 70)
        print(message)
        print("─" * 70)
        return True
    else:
        print("❌ Failed to generate message")
        return False


def test_active_session_status():
    """Test 5: Check active session status"""
    print_header("TEST 5: Active Session Status")

    ceo_context = get_ceo_context()
    if not ceo_context:
        print("❌ No CEO context found")
        return False

    chat_id = ceo_context.get('telegram_chat_id')
    active_session = get_active_session(chat_id)

    if active_session:
        session_id = active_session.get('id')
        state = active_session.get('state')
        created = active_session.get('created_at', '')[:16]

        print(f"✓ Active session found:")
        print(f"  ID: {session_id}")
        print(f"  State: {state}")
        print(f"  Created: {created}")
    else:
        print("✓ No active session (ready for new conversation)")

    return True


def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("  MEMORY SYSTEM TEST SUITE")
    print("=" * 70)

    tests = [
        ("Memory Profile", test_memory_profile),
        ("Recent Sessions", test_recent_sessions),
        ("Memory Consolidation", test_consolidate_memory),
        ("Welcome Back Message", test_welcome_back),
        ("Active Session Status", test_active_session_status)
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Test failed with error: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Print summary
    print_header("TEST SUMMARY")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} | {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed ({passed/total*100:.1f}%)")

    if passed == total:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
