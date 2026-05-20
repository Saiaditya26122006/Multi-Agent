"""
Test Suite for Supabase Client
Tests all database operations for the multi-agent AI system.
"""

import sys
import os
import time

# Add parent directory to path to import memory module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from memory.supabase_client import (
    get_ceo_context,
    get_active_session,
    create_session,
    update_session_state,
    log_event,
    log_message,
    check_message_exists,
    supabase
)


def test_get_ceo_context():
    """Test 1: Fetch CEO context"""
    print("\n" + "="*60)
    print("TEST 1: get_ceo_context()")
    print("="*60)

    try:
        result = get_ceo_context()
        if result:
            print(f"✓ PASS - CEO context found:")
            print(f"  - ID: {result.get('id')}")
            print(f"  - Name: {result.get('name')}")
            print(f"  - Company: {result.get('company')}")
            return result
        else:
            print("✗ FAIL - No CEO context found in database")
            return None
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return None


def test_create_session(ceo_id, telegram_chat_id=123456):
    """Test 2: Create a new session"""
    print("\n" + "="*60)
    print("TEST 2: create_session()")
    print("="*60)

    if not ceo_id:
        print("✗ FAIL - No ceo_id provided (depends on test 1)")
        return None

    try:
        result = create_session(ceo_id, telegram_chat_id)
        if result:
            print(f"✓ PASS - Session created:")
            print(f"  - Session ID: {result.get('id')}")
            print(f"  - State: {result.get('state')}")
            print(f"  - Telegram Chat ID: {result.get('telegram_chat_id')}")
            print(f"  - Awaiting Research: {result.get('awaiting_research')}")
            return result
        else:
            print("✗ FAIL - Session creation returned None")
            return None
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return None


def test_get_active_session(telegram_chat_id=123456):
    """Test 3: Get active session"""
    print("\n" + "="*60)
    print("TEST 3: get_active_session()")
    print("="*60)

    try:
        result = get_active_session(telegram_chat_id)
        if result:
            print(f"✓ PASS - Active session found:")
            print(f"  - Session ID: {result.get('id')}")
            print(f"  - State: {result.get('state')}")
            print(f"  - Started At: {result.get('started_at')}")
            return result
        else:
            print("✗ FAIL - No active session found")
            return None
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return None


def test_log_event(session_id):
    """Test 4: Log an event"""
    print("\n" + "="*60)
    print("TEST 4: log_event()")
    print("="*60)

    if not session_id:
        print("✗ FAIL - No session_id provided (depends on test 2)")
        return None

    try:
        result = log_event(
            agent_id="test_agent",
            action="test_action",
            session_id=session_id,
            state_before="NEEDS_CLARIFICATION",
            state_after="AWAITING_RESEARCH"
        )
        if result:
            print(f"✓ PASS - Event logged:")
            print(f"  - Event ID: {result.get('id')}")
            print(f"  - Agent ID: {result.get('agent_id')}")
            print(f"  - Action: {result.get('action')}")
            print(f"  - State: {result.get('state_before')} → {result.get('state_after')}")
            return result
        else:
            print("✗ FAIL - Event logging returned None")
            return None
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return None


def test_check_message_exists_before(telegram_message_id=999):
    """Test 5: Check if message exists (should be False)"""
    print("\n" + "="*60)
    print("TEST 5: check_message_exists() - Before Insert")
    print("="*60)

    try:
        result = check_message_exists(telegram_message_id)
        if result == False or result is False:
            print(f"✓ PASS - Message {telegram_message_id} does not exist (as expected)")
            return True
        else:
            print(f"✗ FAIL - Message {telegram_message_id} already exists (unexpected)")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_log_message(telegram_message_id=999, session_id=None):
    """Test 6: Log a message"""
    print("\n" + "="*60)
    print("TEST 6: log_message()")
    print("="*60)

    if not session_id:
        print("✗ FAIL - No session_id provided (depends on test 2)")
        return None

    try:
        result = log_message(telegram_message_id, "test message", session_id)
        if result:
            print(f"✓ PASS - Message logged:")
            print(f"  - Message ID: {result.get('id')}")
            print(f"  - Telegram Message ID: {result.get('telegram_message_id')}")
            print(f"  - Content: {result.get('content')}")
            print(f"  - Session ID: {result.get('session_id')}")
            return result
        else:
            print("✗ FAIL - Message logging returned None")
            return None
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return None


def test_check_message_exists_after(telegram_message_id=999):
    """Test 7: Check if message exists (should be True)"""
    print("\n" + "="*60)
    print("TEST 7: check_message_exists() - After Insert")
    print("="*60)

    try:
        result = check_message_exists(telegram_message_id)
        if result is True:
            print(f"✓ PASS - Message {telegram_message_id} exists (as expected)")
            return True
        else:
            print(f"✗ FAIL - Message {telegram_message_id} does not exist (unexpected)")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def cleanup_test_data():
    """Clean up any existing test data before running tests"""
    print("\n" + "="*60)
    print("SETUP - Cleaning test data")
    print("="*60)

    try:
        # Delete test messages FIRST (before sessions)
        result = supabase.table("messages").delete().eq("telegram_message_id", 999).execute()
        print(f"✓ Deleted test messages (telegram_message_id=999) - {len(result.data) if result.data else 0} rows")
    except Exception as e:
        print(f"  Note: Could not delete test messages: {e}")

    try:
        # Delete test sessions SECOND (this will cascade delete related events)
        result = supabase.table("sessions").delete().eq("telegram_chat_id", 123456).execute()
        print(f"✓ Deleted test sessions (telegram_chat_id=123456) - {len(result.data) if result.data else 0} rows")
    except Exception as e:
        print(f"  Note: Could not delete test sessions: {e}")

    print("Cleanup complete - waiting 0.5s for database sync...")
    time.sleep(0.5)


def run_all_tests():
    """Run all tests in sequence"""
    print("\n" + "="*60)
    print("SUPABASE CLIENT TEST SUITE")
    print("="*60)

    # Cleanup existing test data
    cleanup_test_data()

    # Test 1: Get CEO context
    ceo_context = test_get_ceo_context()
    ceo_id = ceo_context.get('id') if ceo_context else None

    # Test 2: Create session
    session = test_create_session(ceo_id, telegram_chat_id=123456)
    session_id = session.get('id') if session else None

    # Test 3: Get active session
    active_session = test_get_active_session(telegram_chat_id=123456)

    # Test 4: Log event
    event = test_log_event(session_id)

    # Test 5: Check message exists (before)
    message_exists_before = test_check_message_exists_before(telegram_message_id=999)

    # Test 6: Log message
    message = test_log_message(telegram_message_id=999, session_id=session_id)

    # Test 7: Check message exists (after)
    message_exists_after = test_check_message_exists_after(telegram_message_id=999)

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    tests_passed = sum([
        ceo_context is not None,
        session is not None,
        active_session is not None,
        event is not None,
        message_exists_before is True,
        message is not None,
        message_exists_after is True
    ])
    total_tests = 7

    print(f"Tests Passed: {tests_passed}/{total_tests}")
    print("="*60 + "\n")

    return tests_passed == total_tests


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
