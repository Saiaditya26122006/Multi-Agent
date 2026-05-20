"""
Comprehensive Test Suite - NO API CALLS
Tests system components without hitting Gemini API limits
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

# Import components
from agents.l0_input_guard import validate_message
from memory.supabase_client import (
    get_ceo_context,
    get_active_session,
    create_session,
    update_session_state,
    get_assumptions_for_session,
    get_decisions_for_session,
    create_assumption,
    create_decision,
    update_decision_status,
    check_message_exists,
    log_message,
    log_event
)


class Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    YELLOW = '\033[93m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header(text):
    """Print colored header"""
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}{Colors.END}")


def print_test(name, passed, details=""):
    """Print test result"""
    status = f"{Colors.GREEN}✅ PASS{Colors.END}" if passed else f"{Colors.RED}❌ FAIL{Colors.END}"
    print(f"{status} | {name}")
    if details:
        color = Colors.GREEN if passed else Colors.YELLOW
        print(f"       {color}{details}{Colors.END}")


def print_summary(passed, failed):
    """Print test summary"""
    total = passed + failed
    success_rate = (passed / total * 100) if total > 0 else 0

    print(f"\n{Colors.BOLD}{'=' * 70}")
    print(f"  TEST SUMMARY")
    print(f"{'=' * 70}{Colors.END}")
    print(f"  Total Tests: {Colors.BOLD}{total}{Colors.END}")
    print(f"  {Colors.GREEN}✅ Passed: {passed}{Colors.END}")
    print(f"  {Colors.RED}❌ Failed: {failed}{Colors.END}")
    print(f"  Success Rate: {Colors.BOLD}{success_rate:.1f}%{Colors.END}")

    if failed == 0:
        print(f"\n{Colors.GREEN}{Colors.BOLD}  🎉 ALL TESTS PASSED!{Colors.END}")
    else:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}  ⚠️  {failed} test(s) failed{Colors.END}")

    print(f"{Colors.BOLD}{'=' * 70}{Colors.END}\n")


def main():
    """Run all tests"""
    passed = 0
    failed = 0

    print(f"\n{Colors.BOLD}{'=' * 70}")
    print(f"  MULTI-AGENT AI SYSTEM - COMPREHENSIVE TEST SUITE")
    print(f"  (No API Calls - Testing Core Functionality)")
    print(f"{'=' * 70}{Colors.END}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}")

    # =================================================================
    # TEST 1: Environment Variables
    # =================================================================
    print_header("TEST 1: Environment Configuration")

    env_vars = {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY"),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
    }

    for var_name, var_value in env_vars.items():
        test_passed = var_value is not None and len(var_value) > 0
        if test_passed:
            passed += 1
        else:
            failed += 1
        print_test(f"{var_name} configured", test_passed, f"Length: {len(var_value) if var_value else 0}")

    # =================================================================
    # TEST 2: Database Connection
    # =================================================================
    print_header("TEST 2: Database Connection & CEO Context")

    try:
        ceo_context = get_ceo_context()
        test_passed = ceo_context is not None
        if test_passed:
            passed += 1
        else:
            failed += 1
        print_test("CEO context loaded", test_passed)

        if ceo_context:
            # Test CEO fields
            fields = {
                "name": ceo_context.get('name'),
                "company": ceo_context.get('company'),
                "telegram_chat_id": ceo_context.get('telegram_chat_id'),
                "strategic_priorities": ceo_context.get('strategic_priorities'),
            }

            for field_name, field_value in fields.items():
                test_passed = field_value is not None
                if test_passed:
                    passed += 1
                else:
                    failed += 1
                print_test(f"CEO has {field_name}", test_passed, f"Value: {str(field_value)[:50]}")

    except Exception as e:
        failed += 1
        print_test("Database connection", False, str(e))

    # =================================================================
    # TEST 3: L0 Input Guard
    # =================================================================
    print_header("TEST 3: L0 Input Guard - Validation & Security")

    ceo_context = get_ceo_context()
    if ceo_context:
        ceo_chat_id = ceo_context.get('telegram_chat_id')

        # Test 3.1: Valid message from CEO
        valid_msg = {
            "message_id": 888888,
            "chat_id": ceo_chat_id,
            "text": "Test message",
            "from_user": {"id": ceo_chat_id}
        }

        try:
            result = validate_message(valid_msg)
            test_passed = result["valid"]
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Valid CEO message accepted", test_passed, result.get("reason", ""))

            test_passed = result.get("session_id") is not None
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Session ID returned", test_passed, f"ID: {result.get('session_id', 'None')[:20]}...")

        except Exception as e:
            failed += 2
            print_test("Valid message processing", False, str(e))

        # Test 3.2: Unauthorized sender
        invalid_msg = {
            "message_id": 888887,
            "chat_id": 999999,
            "text": "Hacker message",
            "from_user": {"id": 999999}
        }

        try:
            result = validate_message(invalid_msg)
            test_passed = not result["valid"]
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Unauthorized sender blocked", test_passed, "Security check working")

            test_passed = "unauthorized" in result.get("reason", "").lower() or "sender" in result.get("reason", "").lower()
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Rejection reason clear", test_passed, result.get("reason", "")[:60])

        except Exception as e:
            failed += 2
            print_test("Security validation", False, str(e))

        # Test 3.3: Duplicate detection
        try:
            result = validate_message(valid_msg)  # Send same message again
            test_passed = not result["valid"]
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Duplicate message detected", test_passed)

            test_passed = "duplicate" in result.get("reason", "").lower()
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Duplicate reason provided", test_passed)

        except Exception as e:
            failed += 2
            print_test("Duplicate detection", False, str(e))

    # =================================================================
    # TEST 4: Session Management
    # =================================================================
    print_header("TEST 4: Session Management")

    ceo_context = get_ceo_context()
    if ceo_context:
        ceo_id = ceo_context.get('id')
        ceo_chat_id = ceo_context.get('telegram_chat_id')

        # Test 4.1: Create new session
        try:
            session = create_session(ceo_id, ceo_chat_id)
            test_passed = session is not None
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Session created", test_passed)

            if session:
                session_id = session.get('id')

                test_passed = session.get('state') == "NEEDS_CLARIFICATION"
                if test_passed:
                    passed += 1
                else:
                    failed += 1
                print_test("Initial state correct", test_passed, f"State: {session.get('state')}")

                # Test 4.2: Get active session
                active = get_active_session(ceo_chat_id)
                test_passed = active is not None
                if test_passed:
                    passed += 1
                else:
                    failed += 1
                print_test("Active session retrieved", test_passed)

                # Test 4.3: Update session state
                updated = update_session_state(session_id, "AWAITING_APPROVAL")
                test_passed = updated is not None
                if test_passed:
                    passed += 1
                else:
                    failed += 1
                print_test("Session state updated", test_passed)

                if updated:
                    test_passed = updated.get('state') == "AWAITING_APPROVAL"
                    if test_passed:
                        passed += 1
                    else:
                        failed += 1
                    print_test("State change verified", test_passed, f"New state: {updated.get('state')}")

                # Test 4.4: Complete session
                completed = update_session_state(session_id, "COMPLETED")
                test_passed = completed is not None
                if test_passed:
                    passed += 1
                else:
                    failed += 1
                print_test("Session completed", test_passed)

        except Exception as e:
            failed += 6
            print_test("Session management", False, str(e))

    # =================================================================
    # TEST 5: Assumptions & Decisions
    # =================================================================
    print_header("TEST 5: Assumptions & Decisions Storage")

    ceo_context = get_ceo_context()
    if ceo_context:
        ceo_id = ceo_context.get('id')
        ceo_chat_id = ceo_context.get('telegram_chat_id')

        try:
            # Create test session
            session = create_session(ceo_id, ceo_chat_id)
            session_id = session.get('id')

            # Test 5.1: Create assumption
            assumption_id = f"test_assumption_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            assumption = create_assumption(
                assumption_id=assumption_id,
                statement="Test assumption statement",
                session_id=session_id,
                confidence="medium",
                clarification_status="pending"
            )

            test_passed = assumption is not None
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Assumption created", test_passed, f"ID: {assumption_id}")

            # Test 5.2: Retrieve assumptions
            assumptions = get_assumptions_for_session(session_id)
            test_passed = len(assumptions) > 0
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Assumptions retrieved", test_passed, f"Count: {len(assumptions)}")

            # Test 5.3: Create decision
            decision_id = f"test_decision_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            decision = create_decision(
                decision_id=decision_id,
                decision="Test decision",
                rationale="Test rationale",
                session_id=session_id,
                assumptions_used=[assumption_id],
                evidence_used=[],
                sections_affected=[],
                status="pending_approval"
            )

            test_passed = decision is not None
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Decision created", test_passed, f"ID: {decision_id}")

            # Test 5.4: Retrieve decisions
            decisions = get_decisions_for_session(session_id)
            test_passed = len(decisions) > 0
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Decisions retrieved", test_passed, f"Count: {len(decisions)}")

            # Test 5.5: Update decision status
            updated = update_decision_status(decision_id, "approved")
            test_passed = updated is True
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Decision status updated", test_passed)

            # Test 5.6: Verify status change
            decisions = get_decisions_for_session(session_id)
            if decisions:
                test_passed = decisions[0].get('status') == "approved"
                if test_passed:
                    passed += 1
                else:
                    failed += 1
                print_test("Status change verified", test_passed, f"Status: {decisions[0].get('status')}")

            # Clean up
            update_session_state(session_id, "COMPLETED")

        except Exception as e:
            failed += 6
            print_test("Assumptions & Decisions", False, str(e))

    # =================================================================
    # TEST 6: Message Logging
    # =================================================================
    print_header("TEST 6: Message & Event Logging")

    ceo_context = get_ceo_context()
    if ceo_context:
        ceo_id = ceo_context.get('id')
        ceo_chat_id = ceo_context.get('telegram_chat_id')

        try:
            session = create_session(ceo_id, ceo_chat_id)
            session_id = session.get('id')

            # Test 6.1: Log message
            msg_id = 777777
            msg_log = log_message(
                telegram_message_id=msg_id,
                content="Test message content",
                session_id=session_id
            )

            test_passed = msg_log is not None
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Message logged", test_passed)

            # Test 6.2: Check message exists
            exists = check_message_exists(msg_id)
            test_passed = exists is True
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Message existence check", test_passed)

            # Test 6.3: Log event
            event = log_event(
                agent_id="TEST_AGENT",
                action="TEST_ACTION",
                session_id=session_id,
                state_before="NEEDS_CLARIFICATION",
                state_after="AWAITING_APPROVAL",
                input_ref="test_input",
                output_ref="test_output"
            )

            test_passed = event is not None
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Event logged", test_passed)

            # Clean up
            update_session_state(session_id, "COMPLETED")

        except Exception as e:
            failed += 3
            print_test("Logging system", False, str(e))

    # =================================================================
    # TEST 7: Data Integrity
    # =================================================================
    print_header("TEST 7: Data Integrity & Relationships")

    ceo_context = get_ceo_context()
    if ceo_context:
        ceo_id = ceo_context.get('id')
        ceo_chat_id = ceo_context.get('telegram_chat_id')

        try:
            # Create full workflow
            session = create_session(ceo_id, ceo_chat_id)
            session_id = session.get('id')

            # Create assumption
            assumption_id = f"integrity_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            create_assumption(
                assumption_id=assumption_id,
                statement="Test",
                session_id=session_id,
                confidence="high",
                clarification_status="resolved"
            )

            # Create decision linked to assumption
            decision_id = f"integrity_decision_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            create_decision(
                decision_id=decision_id,
                decision="Test decision",
                rationale="Test",
                session_id=session_id,
                assumptions_used=[assumption_id],
                evidence_used=[],
                sections_affected=[],
                status="approved"
            )

            # Test 7.1: Session-Assumption link
            assumptions = get_assumptions_for_session(session_id)
            test_passed = any(a.get('assumption_id') == assumption_id for a in assumptions)
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Session-Assumption link", test_passed)

            # Test 7.2: Session-Decision link
            decisions = get_decisions_for_session(session_id)
            test_passed = any(d.get('decision_id') == decision_id for d in decisions)
            if test_passed:
                passed += 1
            else:
                failed += 1
            print_test("Session-Decision link", test_passed)

            # Test 7.3: Decision-Assumption link
            if decisions:
                decision = next((d for d in decisions if d.get('decision_id') == decision_id), None)
                if decision:
                    test_passed = assumption_id in decision.get('assumptions_used', [])
                    if test_passed:
                        passed += 1
                    else:
                        failed += 1
                    print_test("Decision-Assumption link", test_passed)

            # Clean up
            update_session_state(session_id, "COMPLETED")

        except Exception as e:
            failed += 3
            print_test("Data integrity", False, str(e))

    # =================================================================
    # FINAL SUMMARY
    # =================================================================
    print_summary(passed, failed)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
