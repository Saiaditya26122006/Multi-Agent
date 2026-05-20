"""
Test suite for L0 Input Guard Agent
"""

import sys
from pathlib import Path
import random

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.l0_input_guard import validate_message
from memory.supabase_client import get_ceo_context


def test_valid_ceo_message():
    """
    Test 1: Valid CEO message creates session and returns valid=True
    """
    print("\n" + "=" * 60)
    print("TEST 1: Valid CEO Message")
    print("=" * 60)

    # Get CEO context to find the valid chat_id
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ FAIL: No CEO context found in database")
        print("  Please seed the database first")
        return False

    # Get the CEO's telegram_chat_id
    ceo_chat_id = ceo_context.get("telegram_chat_id")
    if not ceo_chat_id:
        print("✗ FAIL: CEO telegram_chat_id not set")
        print("  Please configure telegram_chat_id in ceo_context")
        return False

    # Create a unique test message
    test_message = {
        "message_id": random.randint(100000, 999999),
        "chat_id": ceo_chat_id,
        "text": "Test message: Create new session",
        "from_user": {
            "id": ceo_chat_id,
            "username": "ceo_user",
            "first_name": "CEO"
        }
    }

    print(f"Sending message from chat_id: {ceo_chat_id}")
    result = validate_message(test_message)

    print("\nResult:")
    for key, value in result.items():
        print(f"  {key}: {value}")

    # Assertions
    assert result["valid"] is True, "Expected valid=True for CEO message"
    assert result["session_id"] is not None, "Expected session_id to be set"
    assert result["ceo_id"] is not None, "Expected ceo_id to be set"
    assert result["reason"] is None, "Expected no rejection reason"

    print("\n✓ TEST 1 PASSED")
    return True


def test_duplicate_message():
    """
    Test 2: Duplicate message returns valid=False with reason
    """
    print("\n" + "=" * 60)
    print("TEST 2: Duplicate Message Detection")
    print("=" * 60)

    # Get CEO context
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ FAIL: No CEO context found in database")
        return False

    ceo_chat_id = ceo_context.get("telegram_chat_id")
    if not ceo_chat_id:
        print("✗ FAIL: CEO telegram_chat_id not set")
        return False

    # Send a message first time
    message_id = random.randint(100000, 999999)
    test_message = {
        "message_id": message_id,
        "chat_id": ceo_chat_id,
        "text": "Test message: Send once",
        "from_user": {
            "id": ceo_chat_id,
            "username": "ceo_user",
            "first_name": "CEO"
        }
    }

    print(f"Sending message first time (ID: {message_id})...")
    result1 = validate_message(test_message)

    assert result1["valid"] is True, "First message should be valid"
    print(f"✓ First message accepted: session_id={result1['session_id']}")

    # Send same message again (duplicate)
    print(f"\nSending same message again (ID: {message_id})...")
    result2 = validate_message(test_message)

    print("\nResult:")
    for key, value in result2.items():
        print(f"  {key}: {value}")

    # Assertions
    assert result2["valid"] is False, "Expected valid=False for duplicate message"
    assert result2["reason"] is not None, "Expected rejection reason"
    assert "duplicate" in result2["reason"].lower(), "Expected 'duplicate' in reason"

    print("\n✓ TEST 2 PASSED")
    return True


def test_unknown_sender():
    """
    Test 3: Unknown sender returns valid=False with reason
    """
    print("\n" + "=" * 60)
    print("TEST 3: Unknown Sender Rejection")
    print("=" * 60)

    # Get CEO context
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ FAIL: No CEO context found in database")
        return False

    ceo_chat_id = ceo_context.get("telegram_chat_id")

    if not ceo_chat_id:
        print("✗ FAIL: CEO telegram_chat_id not set")
        print("  This test requires telegram_chat_id to be configured")
        return False

    # Use a different chat_id (not the CEO's)
    unauthorized_chat_id = ceo_chat_id + 12345

    test_message = {
        "message_id": random.randint(100000, 999999),
        "chat_id": unauthorized_chat_id,
        "text": "Test message: Unauthorized sender",
        "from_user": {
            "id": unauthorized_chat_id,
            "username": "unknown_user",
            "first_name": "Unknown"
        }
    }

    print(f"Sending message from unauthorized chat_id: {unauthorized_chat_id}")
    print(f"(CEO's chat_id is: {ceo_chat_id})")
    result = validate_message(test_message)

    print("\nResult:")
    for key, value in result.items():
        print(f"  {key}: {value}")

    # Assertions
    assert result["valid"] is False, "Expected valid=False for unauthorized sender"
    assert result["session_id"] is None, "Expected no session_id for unauthorized sender"
    assert result["reason"] is not None, "Expected rejection reason"
    assert "unauthorized" in result["reason"].lower(), "Expected 'unauthorized' in reason"

    print("\n✓ TEST 3 PASSED")
    return True


def run_all_tests():
    """Run all L0 tests"""
    print("\n" + "=" * 60)
    print("L0 INPUT GUARD - TEST SUITE")
    print("=" * 60)

    tests = [
        ("Valid CEO Message", test_valid_ceo_message),
        ("Duplicate Message Detection", test_duplicate_message),
        ("Unknown Sender Rejection", test_unknown_sender)
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
