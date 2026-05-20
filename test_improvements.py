"""
Test script for 4 improvements:
1. Retry logic
2. Inline keyboards
3. Progress indicator
4. Config file
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "="*80)
print("TESTING 4 IMPROVEMENTS")
print("="*80)

# ============================================================================
# TEST 1: Config File Import
# ============================================================================
print("\n[TEST 1] Testing config file imports...")

try:
    from config.config import (
        MAX_QUESTIONS,
        GEMINI_MODEL,
        GEMINI_FALLBACK_MODEL,
        MAX_RETRIES,
        RETRY_WAIT_SECONDS,
        STATE_NEEDS_CLARIFICATION,
        STATE_AWAITING_APPROVAL,
        STATE_COMPLETED,
        AGENT_L1_CLARITY,
        AGENT_L3_FEEDBACK
    )

    print(f"  ✓ MAX_QUESTIONS = {MAX_QUESTIONS}")
    print(f"  ✓ GEMINI_MODEL = {GEMINI_MODEL}")
    print(f"  ✓ GEMINI_FALLBACK_MODEL = {GEMINI_FALLBACK_MODEL}")
    print(f"  ✓ MAX_RETRIES = {MAX_RETRIES}")
    print(f"  ✓ RETRY_WAIT_SECONDS = {RETRY_WAIT_SECONDS}")
    print(f"  ✓ STATE_NEEDS_CLARIFICATION = {STATE_NEEDS_CLARIFICATION}")
    print(f"  ✓ STATE_AWAITING_APPROVAL = {STATE_AWAITING_APPROVAL}")
    print(f"  ✓ AGENT_L1_CLARITY = {AGENT_L1_CLARITY}")
    print(f"  ✓ AGENT_L3_FEEDBACK = {AGENT_L3_FEEDBACK}")
    print("\n✅ TEST 1 PASSED: Config file working correctly")

except Exception as e:
    print(f"\n✗ TEST 1 FAILED: {e}")
    sys.exit(1)

# ============================================================================
# TEST 2: Retry Decorator
# ============================================================================
print("\n[TEST 2] Testing retry decorator...")

try:
    from utils.retry import retry_with_fallback

    # Test successful call
    @retry_with_fallback(max_retries=3, wait_seconds=1)
    def successful_function():
        return "success"

    result = successful_function()
    assert result == "success", "Successful function should return 'success'"
    print("  ✓ Successful function call works")

    # Test retry on failure (should fail after 3 attempts)
    class AttemptCounter:
        count = 0

    @retry_with_fallback(max_retries=3, wait_seconds=1)
    def failing_function():
        AttemptCounter.count += 1
        raise ValueError(f"Attempt {AttemptCounter.count} failed")

    try:
        failing_function()
        print("  ✗ Should have raised exception after 3 retries")
        sys.exit(1)
    except Exception as e:
        if "Failed after 3 attempts" in str(e):
            print(f"  ✓ Retry decorator correctly failed after {AttemptCounter.count} attempts")
        else:
            print(f"  ✗ Unexpected exception: {e}")
            sys.exit(1)

    print("\n✅ TEST 2 PASSED: Retry logic working correctly")

except Exception as e:
    print(f"\n✗ TEST 2 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# TEST 3: Inline Keyboards
# ============================================================================
print("\n[TEST 3] Testing inline keyboard creation...")

try:
    from tools.telegram_handler import create_decision_keyboard

    keyboard = create_decision_keyboard()

    # Check keyboard structure
    assert keyboard is not None, "Keyboard should not be None"
    assert hasattr(keyboard, 'inline_keyboard'), "Should have inline_keyboard attribute"

    buttons = keyboard.inline_keyboard[0]  # First row of buttons
    assert len(buttons) == 3, f"Should have 3 buttons, got {len(buttons)}"

    # Check button labels
    button_texts = [btn.text for btn in buttons]
    print(f"  ✓ Button 1: {button_texts[0]}")
    print(f"  ✓ Button 2: {button_texts[1]}")
    print(f"  ✓ Button 3: {button_texts[2]}")

    # Check callback data
    callback_data = [btn.callback_data for btn in buttons]
    assert callback_data[0] == "decision_yes", f"Button 1 callback should be 'decision_yes', got {callback_data[0]}"
    assert callback_data[1] == "decision_adjust", f"Button 2 callback should be 'decision_adjust', got {callback_data[1]}"
    assert callback_data[2] == "decision_kill", f"Button 3 callback should be 'decision_kill', got {callback_data[2]}"

    print("  ✓ All button callbacks correct")
    print("\n✅ TEST 3 PASSED: Inline keyboards working correctly")

except Exception as e:
    print(f"\n✗ TEST 3 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# TEST 4: Progress Indicator in L1
# ============================================================================
print("\n[TEST 4] Testing progress indicator in L1 agent...")

try:
    from memory.supabase_client import get_active_session, supabase
    from agents.l1_clarity_agent import generate_clarifying_question

    # Use existing test session
    chat_id = 8866294087
    ceo_id = "b21ddf08-cd2e-4dec-a498-d4f0b4683a43"

    session = get_active_session(chat_id)
    if not session:
        print("  ✗ No active session found for testing")
        sys.exit(1)

    session_id = session['id']
    print(f"  ✓ Using session: {session_id}")

    # Clean up assumptions for this test
    supabase.table("assumptions").delete().eq("session_id", session_id).execute()
    print("  ✓ Cleaned up existing assumptions")

    # Generate first question
    result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text="I want to test the progress indicator"
    )

    question = result.get("question")
    print(f"  ✓ Generated question: {question[:80]}...")

    # Check for progress indicator
    if "Question 1 of 3:" in question or "Question 1 of" in question:
        print("  ✓ Progress indicator found in question")
        print("\n✅ TEST 4 PASSED: Progress indicator working correctly")
    else:
        print(f"  ✗ Progress indicator not found in question: {question}")
        print("\n✗ TEST 4 FAILED: Progress indicator missing")
        sys.exit(1)

except Exception as e:
    print(f"\n✗ TEST 4 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# TEST 5: L1 Agent Uses Config Constants
# ============================================================================
print("\n[TEST 5] Verifying L1 agent uses config constants...")

try:
    # Read L1 agent source code to verify it uses config imports
    l1_source = Path(__file__).parent / "agents" / "l1_clarity_agent.py"
    with open(l1_source, 'r') as f:
        content = f.read()

    checks = [
        ("MAX_QUESTIONS", "MAX_QUESTIONS"),
        ("GEMINI_MODEL", "GEMINI_MODEL"),
        ("MAX_RETRIES", "MAX_RETRIES"),
        ("RETRY_WAIT_SECONDS", "RETRY_WAIT_SECONDS"),
        ("STATE_NEEDS_CLARIFICATION", "STATE_NEEDS_CLARIFICATION"),
        ("AGENT_L1_CLARITY", "AGENT_L1_CLARITY")
    ]

    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ L1 uses {check_name}")
        else:
            print(f"  ✗ L1 does NOT use {check_name}")
            sys.exit(1)

    # Check that hardcoded values are gone
    if 'model=\'gemini-2.0-flash\'' in content or 'model="gemini-2.0-flash"' in content:
        print("  ✗ L1 still has hardcoded model name")
        sys.exit(1)
    else:
        print("  ✓ L1 does NOT have hardcoded model name")

    print("\n✅ TEST 5 PASSED: L1 agent uses config constants")

except Exception as e:
    print(f"\n✗ TEST 5 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# TEST 6: L3 Agent Uses Config Constants
# ============================================================================
print("\n[TEST 6] Verifying L3 agent uses config constants...")

try:
    # Read L3 agent source code to verify it uses config imports
    l3_source = Path(__file__).parent / "agents" / "l3_feedback_agent.py"
    with open(l3_source, 'r') as f:
        content = f.read()

    checks = [
        ("GEMINI_MODEL", "GEMINI_MODEL"),
        ("MAX_RETRIES", "MAX_RETRIES"),
        ("RETRY_WAIT_SECONDS", "RETRY_WAIT_SECONDS"),
        ("STATE_NEEDS_CLARIFICATION", "STATE_NEEDS_CLARIFICATION"),
        ("STATE_AWAITING_APPROVAL", "STATE_AWAITING_APPROVAL"),
        ("AGENT_L3_FEEDBACK", "AGENT_L3_FEEDBACK")
    ]

    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ L3 uses {check_name}")
        else:
            print(f"  ✗ L3 does NOT use {check_name}")
            sys.exit(1)

    # Check that hardcoded values are gone
    if 'model=\'gemini-2.0-flash\'' in content or 'model="gemini-2.0-flash"' in content:
        print("  ✗ L3 still has hardcoded model name")
        sys.exit(1)
    else:
        print("  ✓ L3 does NOT have hardcoded model name")

    print("\n✅ TEST 6 PASSED: L3 agent uses config constants")

except Exception as e:
    print(f"\n✗ TEST 6 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# FINAL RESULTS
# ============================================================================
print("\n" + "="*80)
print("ALL TESTS PASSED ✅")
print("="*80)
print("\nSummary:")
print("  ✅ Config file created and working")
print("  ✅ Retry decorator implemented and tested")
print("  ✅ Inline keyboards created and structured correctly")
print("  ✅ Progress indicator added to L1 questions")
print("  ✅ L1 agent uses config constants (no hardcoded values)")
print("  ✅ L3 agent uses config constants (no hardcoded values)")
print("\n" + "="*80)
print("READY FOR PRODUCTION")
print("="*80 + "\n")

sys.exit(0)
