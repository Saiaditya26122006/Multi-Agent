"""
Test Suite for Session State Management
Tests session state operations via Supabase (replaces Redis).
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from memory.supabase_client import (
    get_session_flag,
    set_session_flag,
    get_session_data,
    set_session_data,
    clear_session_flags,
    create_session,
    get_ceo_context,
)

# Test data
TEST_CEO_ID = "test-ceo-session-state"
TEST_CHAT_ID = "test-chat-session-state"


def setup_test_session():
    """Create a test session in Supabase."""
    print("\n" + "="*60)
    print("SETUP - Creating test session")
    print("="*60)

    try:
        session = create_session(TEST_CEO_ID, TEST_CHAT_ID)
        session_id = session["id"]
        print(f"✓ Created test session: {session_id}")
        return session_id
    except Exception as e:
        print(f"✗ Failed to create session: {e}")
        return None


def test_set_get_boolean_flag(session_id):
    """Test setting and getting boolean flags."""
    print("\n" + "="*60)
    print("TEST 1: Set/Get Boolean Flag")
    print("="*60)

    try:
        # Set flag
        result = set_session_flag(session_id, "awaiting_clarification", True)
        if not result:
            print("✗ FAIL - set_session_flag returned False")
            return False

        # Get flag
        value = get_session_flag(session_id, "awaiting_clarification")
        if value == True:
            print(f"✓ PASS - Flag set and retrieved correctly")
            print(f"  - Session ID: {session_id}")
            print(f"  - Flag: awaiting_clarification")
            print(f"  - Value: {value}")
            return True
        else:
            print(f"✗ FAIL - Expected True but got: {value}")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_set_get_data(session_id):
    """Test setting and getting data fields."""
    print("\n" + "="*60)
    print("TEST 2: Set/Get Data Field")
    print("="*60)

    try:
        test_question = "What's your target market?"

        # Set data
        result = set_session_data(session_id, "last_question", test_question)
        if not result:
            print("✗ FAIL - set_session_data returned False")
            return False

        # Get data
        value = get_session_data(session_id, "last_question")
        if value == test_question:
            print(f"✓ PASS - Data set and retrieved correctly")
            print(f"  - Session ID: {session_id}")
            print(f"  - Field: last_question")
            print(f"  - Value: {value}")
            return True
        else:
            print(f"✗ FAIL - Expected '{test_question}' but got: {value}")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_set_get_json_data(session_id):
    """Test setting and getting JSON data."""
    print("\n" + "="*60)
    print("TEST 3: Set/Get JSON Data")
    print("="*60)

    try:
        test_data = {
            "task_id": "task-123",
            "run_id": "run-456"
        }

        # Set data
        result = set_session_data(session_id, "clarification_data", test_data)
        if not result:
            print("✗ FAIL - set_session_data returned False")
            return False

        # Get data
        value = get_session_data(session_id, "clarification_data")
        if value == test_data:
            print(f"✓ PASS - JSON data set and retrieved correctly")
            print(f"  - Session ID: {session_id}")
            print(f"  - Field: clarification_data")
            print(f"  - Value: {value}")
            return True
        else:
            print(f"✗ FAIL - Expected {test_data} but got: {value}")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_default_values(session_id):
    """Test that missing values return defaults."""
    print("\n" + "="*60)
    print("TEST 4: Default Values")
    print("="*60)

    try:
        # Get non-existent flag with default
        value = get_session_flag(session_id, "nonexistent_flag", False)
        if value == False:
            print(f"✓ PASS - Got default value for missing flag")
            print(f"  - Flag: nonexistent_flag")
            print(f"  - Default: False")
            print(f"  - Actual: {value}")
            return True
        else:
            print(f"✗ FAIL - Expected False but got: {value}")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_multiple_flags(session_id):
    """Test setting multiple flags."""
    print("\n" + "="*60)
    print("TEST 5: Multiple Flags")
    print("="*60)

    try:
        # Set multiple flags
        flags = {
            "awaiting_clarification": True,
            "awaiting_approval": False,
            "gate2_active": True,
        }

        for flag_name, value in flags.items():
            set_session_flag(session_id, flag_name, value)

        # Verify all flags
        all_correct = True
        for flag_name, expected_value in flags.items():
            actual_value = get_session_flag(session_id, flag_name)
            if actual_value != expected_value:
                print(f"✗ Flag '{flag_name}' mismatch: expected {expected_value}, got {actual_value}")
                all_correct = False

        if all_correct:
            print(f"✓ PASS - All flags set and retrieved correctly")
            for flag_name, value in flags.items():
                print(f"  - {flag_name}: {value}")
            return True
        else:
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def test_clear_flags(session_id):
    """Test clearing all flags."""
    print("\n" + "="*60)
    print("TEST 6: Clear All Flags")
    print("="*60)

    try:
        # Set some flags
        set_session_flag(session_id, "flag1", True)
        set_session_flag(session_id, "flag2", True)

        # Clear all flags
        result = clear_session_flags(session_id)
        if not result:
            print("✗ FAIL - clear_session_flags returned False")
            return False

        # Verify flags are cleared (should return defaults)
        flag1 = get_session_flag(session_id, "flag1", False)
        flag2 = get_session_flag(session_id, "flag2", False)

        if flag1 == False and flag2 == False:
            print(f"✓ PASS - All flags cleared successfully")
            return True
        else:
            print(f"✗ FAIL - Flags not cleared: flag1={flag1}, flag2={flag2}")
            return False
    except Exception as e:
        print(f"✗ FAIL - Exception: {e}")
        return False


def run_all_tests():
    """Run all tests in sequence."""
    print("\n" + "="*60)
    print("SESSION STATE TEST SUITE (Supabase)")
    print("="*60)

    # Setup
    session_id = setup_test_session()
    if not session_id:
        print("\n✗ FAILED - Could not create test session")
        return False

    # Run tests
    test1_pass = test_set_get_boolean_flag(session_id)
    test2_pass = test_set_get_data(session_id)
    test3_pass = test_set_get_json_data(session_id)
    test4_pass = test_default_values(session_id)
    test5_pass = test_multiple_flags(session_id)
    test6_pass = test_clear_flags(session_id)

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
