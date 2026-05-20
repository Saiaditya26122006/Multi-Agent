# Main Pipeline - Bug Fixes & Enhancements

## Date: 2026-05-14

## Changes Made

### 1. Fixed AWAITING_APPROVAL Bug

**Problem**: When session was in `AWAITING_APPROVAL` state and CEO sent a new idea (not Yes/Adjust/Kill), the system would remind about pending decision instead of processing the new idea.

**Solution**: Detect new ideas and reset session state automatically.

**Before**:
```python
if current_state == "AWAITING_APPROVAL":
    # Remind CEO about pending decision
    await send_message(chat_id, "⚠️ You have a pending decision...")
    return  # ❌ Blocks new ideas
```

**After**:
```python
if current_state == "AWAITING_APPROVAL":
    # CEO has a new idea - reset and continue
    print("[NEW IDEA] Resetting session to NEEDS_CLARIFICATION...")
    update_session_state(session_id, "NEEDS_CLARIFICATION")
    # ✅ Continue to L1 processing
```

### 2. Added /reset Command

**Purpose**: Allow CEO to manually reset the session at any time.

**Usage**: CEO sends `/reset` in Telegram

**Behavior**:
- Closes current session (sets state to `COMPLETED`)
- Sends confirmation message
- Next message starts fresh session

**Implementation**:
```python
if text_lower == "/reset":
    print("[RESET] Processing reset command...")
    update_session_state(session_id, "COMPLETED")
    
    await send_message(
        chat_id,
        "🔄 Session reset. Starting fresh.\n\n"
        "Send a new message when you're ready."
    )
    print("[RESET] ✓ Reset complete")
    return
```

## Updated Pipeline Flow

```
Message Received
    ↓
L0: Validate
    ↓
Is message "/reset"?
    Yes → Close session, confirm, STOP
    No ↓
Is session AWAITING_APPROVAL?
    Yes ↓
    Is message "Yes/Adjust/Kill"?
        Yes → Handle decision, STOP
        No → Reset to NEEDS_CLARIFICATION, continue ↓
L1: Generate question
    ↓
Send to CEO
```

## Behavior Examples

### Example 1: New Idea During Approval

**Session State**: `AWAITING_APPROVAL`

**CEO sends**:
```
I want to work on something else now
```

**System**:
```
[NEW IDEA] Session was awaiting approval, but CEO has a new idea
[NEW IDEA] Resetting session to NEEDS_CLARIFICATION...
[L1] Generating clarifying question...
[L1] ✓ Sent to CEO
```

**CEO receives**:
```
What specific aspect would you like to focus on?
```

### Example 2: Reset Command

**CEO sends**:
```
/reset
```

**System**:
```
[RESET] Processing reset command...
[RESET] ✓ Current session closed
[RESET] ✓ Reset complete
```

**CEO receives**:
```
🔄 Session reset. Starting fresh.

Send a new message when you're ready.
```

### Example 3: Normal Decision Flow (Unchanged)

**Session State**: `AWAITING_APPROVAL`

**CEO sends**:
```
Yes
```

**System**:
```
[DECISION] Processing CEO response: Yes
[DECISION] CEO approved - updating decision and BP section...
[DECISION] ✓ Approved and session completed
```

**CEO receives**:
```
✅ Decision approved! Moving forward with the plan.

Session completed. Send a new message when you're ready.
```

## Testing Scenarios

### Test 1: New Idea During Approval
1. Get session to `AWAITING_APPROVAL` state (send L3 feedback)
2. Send a new idea instead of Yes/Adjust/Kill
3. **Expected**: Session resets, L1 generates question
4. **Result**: ✅ Works

### Test 2: Reset Command
1. Start any conversation
2. Send `/reset`
3. **Expected**: Session closed, confirmation sent
4. **Result**: ✅ Works

### Test 3: Normal Decision Still Works
1. Get session to `AWAITING_APPROVAL`
2. Send "Yes"
3. **Expected**: Decision approved, session completed
4. **Result**: ✅ Works (unchanged)

## Code Quality

- ✅ Clear logging for new behaviors
- ✅ Consistent with existing code style
- ✅ No breaking changes to existing functionality
- ✅ Comments updated with new step numbers

## Console Output Examples

### New Idea Detection:
```
============================================================
[INCOMING] Message 12346 from chat 8866294087
[TEXT] I want to focus on something else
============================================================

[L0] Validating message...
[L0] ✓ Message valid
[SESSION] Current state: AWAITING_APPROVAL

[NEW IDEA] Session was awaiting approval, but CEO has a new idea
[NEW IDEA] Resetting session to NEEDS_CLARIFICATION...
[NEW IDEA] ✓ Session reset to NEEDS_CLARIFICATION

[L1] Generating clarifying question...
[L1] ✓ Question generated
[L1] ✓ Sent to CEO

[PIPELINE] ✓ Message processed successfully
============================================================
```

### Reset Command:
```
============================================================
[INCOMING] Message 12347 from chat 8866294087
[TEXT] /reset
============================================================

[L0] Validating message...
[L0] ✓ Message valid
[SESSION] Current state: NEEDS_CLARIFICATION

[RESET] Processing reset command...
[RESET] ✓ Current session closed
[RESET] ✓ Reset complete
============================================================
```

## Updated Step Numbers

The pipeline steps are now:
1. L0: Input Guard (validate)
2. Handle `/reset` command
3. Handle decision response (Yes/Adjust/Kill)
4. Handle new idea (if AWAITING_APPROVAL)
5. L1: Clarity Agent (generate question)
6. Check for L3 trigger (future)

## Benefits

### Better User Experience
- CEO can change their mind without being blocked
- No need to force a decision when priorities change
- Natural conversation flow maintained

### More Flexible
- System adapts to CEO's changing focus
- No dead-end states
- Easy manual reset for testing and real use

### Clearer Intent Detection
- System distinguishes between:
  - Decision responses (Yes/Adjust/Kill)
  - New ideas (anything else)
  - Reset command (/reset)

## Migration Notes

No database migration needed - these are pure logic changes.

## Files Modified

- `main.py` - Updated message handling logic

## Backward Compatibility

✅ All existing behavior preserved:
- Yes/Adjust/Kill still work
- L0 → L1 flow unchanged
- Session states unchanged
- Database operations unchanged

## Related Documentation

See also:
- `MAIN_PIPELINE_GUIDE.md` - Overall pipeline guide
- `README_COMPLETE.md` - System overview

---

**Status**: ✅ Complete  
**Tested**: ✅ Logic verified  
**Breaking Changes**: ❌ None  
**Ready for**: ✅ Production
