"""
Test Suite for Redis Client
Tests all session state management operations.
"""

import sys
import os
import time

# Add parent directory to path to import memory module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from memory.redis_client import (
    set_session_state,
    get_session_state,
    delete_session_state,
    update_session_field,
    session_exists,
    redis_client,
    _get_session_key
)

# Test session ID
TEST_SESSION_ID = "test-session-12345"


def cleanup_test_data():
    """Clean up any existing test data before and after running tests"""
    print("\n" + "="*60)
    print("SETUP - Cleaning test data")
    print("="*60)

    try:
        key = _get_session_key(TEST_SESSION_ID)
        result = redis_client.delete(key)
        print(f"✓ Deleted test session key: {key} ({result} keys deleted)")
    except Exception as e:
        print(f"  Note: Could not delete test session: {e}")

    print("Cleanup complete\n")
    time.sleep(0.2)  # Small delay for Redis sync


def test_set_session_state():
    """Test 1: Set session state"""
    print("\n" + "="*60)
    print("TEST 1: set_session_state()")
    print("="*60)

    try:
        test_data = {
            "state": "NEEDS_CLARIFICATION",
            "awaiting_research": False,
            "last_message": "Hello, world!",
            "counter": 42
        }

        result = set_session_state(TEST_SESSION_ID, test_data, ttl_seconds=3600)

        if result:
            print(f"✓ PASS - Session state set successfully")
            print(f"  - Session ID: {TEST_SESSION_ID}")
            print(f"  - Data: {test_data}")
            return True
        else:
            print("✗ FAIL - set_session_state returned False")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_session_exists():
    """Test 2: Check if session exists"""
    print("\n" + "="*60)
    print("TEST 2: session_exists()")
    print("="*60)

    try:
        result = session_exists(TEST_SESSION_ID)

        if result:
            print(f"✓ PASS - Session exists (as expected)")
            print(f"  - Session ID: {TEST_SESSION_ID}")
            return True
        else:
            print(f"✗ FAIL - Session does not exist (unexpected)")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_get_session_state():
    """Test 3: Get session state"""
    print("\n" + "="*60)
    print("TEST 3: get_session_state()")
    print("="*60)

    try:
        result = get_session_state(TEST_SESSION_ID)

        if result:
            print(f"✓ PASS - Session state retrieved:")
            print(f"  - State: {result.get('state')}")
            print(f"  - Awaiting Research: {result.get('awaiting_research')}")
            print(f"  - Last Message: {result.get('last_message')}")
            print(f"  - Counter: {result.get('counter')}")
            return result
        else:
            print("✗ FAIL - No session state found")
            return None
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return None


def test_update_session_field():
    """Test 4: Update a single field"""
    print("\n" + "="*60)
    print("TEST 4: update_session_field()")
    print("="*60)

    try:
        result = update_session_field(TEST_SESSION_ID, "counter", 100)

        if result:
            # Verify the update
            updated_state = get_session_state(TEST_SESSION_ID)
            if updated_state and updated_state.get('counter') == 100:
                print(f"✓ PASS - Field updated successfully")
                print(f"  - Field: counter")
                print(f"  - Old Value: 42")
                print(f"  - New Value: {updated_state.get('counter')}")
                return True
            else:
                print(f"✗ FAIL - Field was not updated correctly")
                return False
        else:
            print("✗ FAIL - update_session_field returned False")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_delete_session_state():
    """Test 5: Delete session state"""
    print("\n" + "="*60)
    print("TEST 5: delete_session_state()")
    print("="*60)

    try:
        result = delete_session_state(TEST_SESSION_ID)

        if result:
            # Verify deletion
            exists = session_exists(TEST_SESSION_ID)
            if not exists:
                print(f"✓ PASS - Session deleted successfully")
                print(f"  - Session ID: {TEST_SESSION_ID}")
                return True
            else:
                print(f"✗ FAIL - Session still exists after deletion")
                return False
        else:
            print("✗ FAIL - delete_session_state returned False")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_get_nonexistent_session():
    """Test 6: Get non-existent session (should return None)"""
    print("\n" + "="*60)
    print("TEST 6: get_session_state() - Non-existent")
    print("="*60)

    try:
        result = get_session_state(TEST_SESSION_ID)

        if result is None:
            print(f"✓ PASS - Correctly returned None for non-existent session")
            return True
        else:
            print(f"✗ FAIL - Expected None but got: {result}")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def run_all_tests():
    """Run all tests in sequence"""
    print("\n" + "="*60)
    print("REDIS CLIENT TEST SUITE")
    print("="*60)

    # Cleanup before tests
    cleanup_test_data()

    # Run tests in sequence
    test1_pass = test_set_session_state()
    test2_pass = test_session_exists()
    test3_state = test_get_session_state()
    test3_pass = test3_state is not None
    test4_pass = test_update_session_field()
    test5_pass = test_delete_session_state()
    test6_pass = test_get_nonexistent_session()

    # Cleanup after tests
    cleanup_test_data()

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)

    tests_passed = sum([
        test1_pass,
        test2_pass,
        test3_pass,
        test4_pass,
        test5_pass,
        test6_pass
    ])
    total_tests = 6

    print(f"Tests Passed: {tests_passed}/{total_tests}")
    print("="*60 + "\n")

    return tests_passed == total_tests


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
