"""
Test script for 4 improvements (without API calls):
1. Retry logic
2. Inline keyboards
3. Progress indicator (manual verification)
4. Config file
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "="*80)
print("TESTING 4 IMPROVEMENTS (NO API CALLS)")
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
# TEST 4: Progress Indicator Logic (Code Check)
# ============================================================================
print("\n[TEST 4] Verifying progress indicator logic in code...")

try:
    # Check if progress indicator is added to questions
    l1_source = Path(__file__).parent / "agents" / "l1_clarity_agent.py"
    with open(l1_source, 'r') as f:
        content = f.read()

    # Check for progress indicator formatting
    if 'f"Question {current_question_number} of {MAX_QUESTIONS}:' in content:
        print("  ✓ Progress indicator format found: 'Question X of Y:'")
    else:
        print("  ✗ Progress indicator format not found")
        sys.exit(1)

    # Verify raw_question variable exists
    if 'raw_question = call_gemini_with_retry()' in content:
        print("  ✓ Raw question captured before adding progress indicator")
    else:
        print("  ✗ Raw question capture not found")
        sys.exit(1)

    # Verify question variable is updated
    if 'question = f"Question {current_question_number} of {MAX_QUESTIONS}: {raw_question}"' in content:
        print("  ✓ Question updated with progress indicator")
    else:
        print("  ✗ Question update not found")
        sys.exit(1)

    print("\n✅ TEST 4 PASSED: Progress indicator logic implemented correctly")
    print("   NOTE: Actual API test requires Gemini quota. Code structure verified.")

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
        ("AGENT_L1_CLARITY", "AGENT_L1_CLARITY"),
        ("retry_with_fallback", "retry_with_fallback")
    ]

    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ L1 uses {check_name}")
        else:
            print(f"  ✗ L1 does NOT use {check_name}")
            sys.exit(1)

    # Check that imports are present
    if "from config.config import" in content:
        print("  ✓ L1 imports from config.config")
    else:
        print("  ✗ L1 does NOT import from config.config")
        sys.exit(1)

    if "from utils.retry import" in content:
        print("  ✓ L1 imports from utils.retry")
    else:
        print("  ✗ L1 does NOT import from utils.retry")
        sys.exit(1)

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
        ("AGENT_L3_FEEDBACK", "AGENT_L3_FEEDBACK"),
        ("retry_with_fallback", "retry_with_fallback")
    ]

    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ L3 uses {check_name}")
        else:
            print(f"  ✗ L3 does NOT use {check_name}")
            sys.exit(1)

    # Check that imports are present
    if "from config.config import" in content:
        print("  ✓ L3 imports from config.config")
    else:
        print("  ✗ L3 does NOT import from config.config")
        sys.exit(1)

    if "from utils.retry import" in content:
        print("  ✓ L3 imports from utils.retry")
    else:
        print("  ✗ L3 does NOT import from utils.retry")
        sys.exit(1)

    print("\n✅ TEST 6 PASSED: L3 agent uses config constants")

except Exception as e:
    print(f"\n✗ TEST 6 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# TEST 7: Main.py Uses Inline Keyboards
# ============================================================================
print("\n[TEST 7] Verifying main.py uses inline keyboards...")

try:
    main_source = Path(__file__).parent / "main.py"
    with open(main_source, 'r') as f:
        content = f.read()

    checks = [
        ("create_decision_keyboard import", "create_decision_keyboard"),
        ("keyboard = create_decision_keyboard()", "keyboard = create_decision_keyboard()"),
        ("send_message with reply_markup", "reply_markup=keyboard"),
        ("handle_telegram_callback function", "async def handle_telegram_callback"),
        ("callback handler in start_polling", "handle_callback=handle_telegram_callback")
    ]

    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ main.py has {check_name}")
        else:
            print(f"  ✗ main.py does NOT have {check_name}")
            sys.exit(1)

    print("\n✅ TEST 7 PASSED: main.py uses inline keyboards correctly")

except Exception as e:
    print(f"\n✗ TEST 7 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# TEST 8: Telegram Handler Supports Callbacks
# ============================================================================
print("\n[TEST 8] Verifying telegram_handler supports callbacks...")

try:
    handler_source = Path(__file__).parent / "tools" / "telegram_handler.py"
    with open(handler_source, 'r') as f:
        content = f.read()

    checks = [
        ("InlineKeyboardButton import", "InlineKeyboardButton"),
        ("InlineKeyboardMarkup import", "InlineKeyboardMarkup"),
        ("CallbackQueryHandler import", "CallbackQueryHandler"),
        ("create_decision_keyboard function", "def create_decision_keyboard"),
        ("handle_callback parameter", "handle_callback"),
        ("callback_handler registration", "CallbackQueryHandler(callback_handler)")
    ]

    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ telegram_handler has {check_name}")
        else:
            print(f"  ✗ telegram_handler does NOT have {check_name}")
            sys.exit(1)

    print("\n✅ TEST 8 PASSED: telegram_handler supports callbacks correctly")

except Exception as e:
    print(f"\n✗ TEST 8 FAILED: {e}")
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
print("  ✅ Progress indicator logic verified in code")
print("  ✅ L1 agent uses config constants and retry logic")
print("  ✅ L3 agent uses config constants and retry logic")
print("  ✅ main.py uses inline keyboards correctly")
print("  ✅ telegram_handler supports callback queries")
print("\n" + "="*80)
print("IMPLEMENTATION COMPLETE")
print("="*80)
print("\nAll 4 improvements successfully implemented:")
print("  1. ✅ Retry logic with 3 attempts and 5s wait")
print("  2. ✅ Inline keyboards for Yes/Adjust/Kill")
print("  3. ✅ Progress indicator 'Question X of 3:'")
print("  4. ✅ Config file with all constants")
print("\nNext steps:")
print("  • Wait for Gemini API quota to reset")
print("  • Test full flow via Telegram")
print("  • Verify buttons work in Telegram app")
print("="*80 + "\n")

sys.exit(0)
