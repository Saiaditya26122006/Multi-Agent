# L1 Question Limit Fix - Implementation Summary

## Date: 2026-05-14

## Critical Bug Fixed

**Problem**: L1 Clarity Agent had no question counter. It asked 7+ questions in a single session when maximum should be 3. Questions repeated and drifted off-topic.

**Root Cause**: No mechanism to track how many questions had been asked or trigger L3 after 3 questions.

---

## Solution Implemented

### 1. Added Question Counter to L1 Agent

**File**: `agents/l1_clarity_agent.py`

**Changes**:

#### A. Import `get_assumptions_for_session`
```python
from memory.supabase_client import (
    ...
    get_assumptions_for_session  # NEW
)
```

#### B. Check question count at start of function
```python
def generate_clarifying_question(...) -> Dict[str, Any]:
    print(f"[L1] Processing message for session {session_id}")

    # CRITICAL: Check question counter - maximum 3 questions per session
    existing_assumptions = get_assumptions_for_session(session_id)
    question_count = len(existing_assumptions)

    print(f"[L1] Questions asked so far: {question_count}/3")

    if question_count >= 3:
        print("[L1] ✓ Maximum questions reached (3/3)")
        print("[L1] ✓ Clarification phase complete, ready for L3")
        return {
            "clarification_complete": True,
            "session_id": session_id,
            "question": None,
            "assumption_id": None
        }
```

#### C. Updated prompt to include question number
```python
current_question_number = question_count + 1
prompt = f"""You are a clarity agent helping a CEO build their business plan. This is question {current_question_number} of 3 maximum questions.

{context}

CEO MESSAGE: "{message_text}"

RULES:
1. This is question {current_question_number}/3 - make it count
2. Ask ONE specific, focused question to clarify the CEO's intent
3. DO NOT ask about information already in the CEO context card
4. DO NOT repeat questions you've already asked (check unresolved assumptions above)
5. Focus on understanding what the CEO wants to accomplish
6. Keep the question short and direct
7. If the message is vague, ask about the most important missing detail
8. Since you only have {3 - question_count} questions left, prioritize the most critical information

OUTPUT FORMAT:
Return ONLY the question text, nothing else. No preamble, no explanation.

QUESTION:"""
```

#### D. Updated return value
```python
return {
    "question": question,
    "assumption_id": assumption_id,
    "session_id": session_id,
    "clarification_complete": False  # NEW field
}
```

---

### 2. Added Auto L3 Trigger to Main Pipeline

**File**: `main.py`

**Changes in STEP 5 (L1 processing)**:

```python
# STEP 5: L1 CLARITY AGENT - Generate clarifying question
print("\n[L1] Generating clarifying question...")

try:
    l1_result = generate_clarifying_question(
        session_id=session_id,
        ceo_id=ceo_id,
        message_text=text
    )

    # Check if clarification is complete (3 questions already asked)
    if l1_result.get("clarification_complete"):
        print("[L1] ✓ Clarification complete (3/3 questions answered)")
        print("[L1] ✓ Triggering L3 Feedback Agent...")

        # Trigger L3 to generate feedback
        from agents.l3_feedback_agent import generate_feedback

        print("\n[L3] Generating feedback based on clarification...")

        l3_result = generate_feedback(
            session_id=session_id,
            research_brief=None  # No research brief yet, L3 will work with assumptions
        )

        telegram_message = l3_result.get("telegram_message")

        if telegram_message:
            await send_message(chat_id, telegram_message)
            print("[L3] ✓ Feedback sent to CEO")
            print(f"[L3] Decision ID: {l3_result.get('decision_id')}")
        else:
            await send_message(
                chat_id,
                "⚠️ Error generating feedback. Please try again."
            )
            print("[L3] ✗ No telegram message generated")

        print("[PIPELINE] ✓ L1 → L3 transition complete")
        print("=" * 60 + "\n")
        return

    # Normal L1 flow - send question to CEO
    question = l1_result["question"]
    assumption_id = l1_result["assumption_id"]

    print(f"[L1] ✓ Question generated")
    print(f"[L1] Assumption: {assumption_id}")

    # Send clarifying question to CEO
    await send_message(chat_id, question)
    print(f"[L1] ✓ Sent to CEO: {question[:80]}...")
```

---

### 3. Database Cleanup

Reset all non-completed sessions:

```sql
UPDATE sessions 
SET state = 'NEEDS_CLARIFICATION' 
WHERE state != 'COMPLETED';
```

**Result**: 1 session reset to NEEDS_CLARIFICATION

---

## Updated Flow

### Before (Buggy):
```
CEO: Vague message
  ↓
L1: Question 1
CEO: Answer 1
  ↓
L1: Question 2
CEO: Answer 2
  ↓
L1: Question 3
CEO: Answer 3
  ↓
L1: Question 4 (REPEAT)
CEO: Answer 4
  ↓
L1: Question 5 (OFF TOPIC)
CEO: Answer 5
  ↓
... continues forever, no L3 trigger
```

### After (Fixed):
```
CEO: Vague message
  ↓
L1: Question 1/3
CEO: Answer 1
  ↓
L1: Question 2/3
CEO: Answer 2
  ↓
L1: Question 3/3
CEO: Answer 3
  ↓
L1: Detects 3 questions answered
  ↓
L3: Auto-triggered
  ↓
CEO: Receives feedback summary with Yes/Adjust/Kill decision
```

---

## Expected Console Output

### Question 1:
```
[L1] Processing message for session 28241cb4...
[L1] Questions asked so far: 0/3
[L1] ✓ Loaded CEO context: Alex Zamurko
[L1] ✓ Project state loaded:
     - Open sections: 5
     - Unresolved assumptions: 0
     - Pending decisions: 0
[L1] ✓ Generating clarifying question with Gemini...
[L1] ✓ Generated question: What specific aspect of growth...
[L1] ✓ Created assumption: assumption_20260514_224500
[L1] ✓ Updated session state to NEEDS_CLARIFICATION
[L1] ✓ Event logged
[L1] ✅ Clarifying question generated successfully (1/3)
[L1] ✓ Sent to CEO: What specific aspect of growth...
```

### Question 2:
```
[L1] Processing message for session 28241cb4...
[L1] Questions asked so far: 1/3
[L1] ✓ Loaded CEO context: Alex Zamurko
...
[L1] ✅ Clarifying question generated successfully (2/3)
[L1] ✓ Sent to CEO: Which markets are you targeting...
```

### Question 3:
```
[L1] Processing message for session 28241cb4...
[L1] Questions asked so far: 2/3
[L1] ✓ Loaded CEO context: Alex Zamurko
...
[L1] ✅ Clarifying question generated successfully (3/3)
[L1] ✓ Sent to CEO: What is your budget for this expansion...
```

### After Question 3 Answer:
```
[L1] Processing message for session 28241cb4...
[L1] Questions asked so far: 3/3
[L1] ✓ Maximum questions reached (3/3)
[L1] ✓ Clarification phase complete, ready for L3
[L1] ✓ Clarification complete (3/3 questions answered)
[L1] ✓ Triggering L3 Feedback Agent...

[L3] Generating feedback based on clarification...
[L3] Processing session 28241cb4...
[L3] ✓ Loaded CEO context: Alex Zamurko
[L3] ✓ Loaded 3 assumptions
[L3] ✓ Generating feedback with Gemini...
[L3] ✓ Feedback generated
[L3] ✓ Created decision: decision_20260514_224600
[L3] ✓ Session state updated to AWAITING_APPROVAL
[L3] ✓ Agent output saved
[L3] ✓ Event logged
[L3] ✅ Feedback generated successfully
[L3] ✓ Feedback sent to CEO
[L3] Decision ID: decision_20260514_224600

[PIPELINE] ✓ L1 → L3 transition complete
============================================================
```

---

## Testing Checklist

To verify the fix works:

- [ ] Start main.py: `python3 main.py`
- [ ] Send vague message via Telegram: "I want to grow my business"
- [ ] Receive question 1/3 (check console logs for "1/3")
- [ ] Answer question 1
- [ ] Receive question 2/3 (check console logs for "2/3")
- [ ] Answer question 2
- [ ] Receive question 3/3 (check console logs for "3/3")
- [ ] Answer question 3
- [ ] Verify L1 returns `clarification_complete: True`
- [ ] Verify L3 is auto-triggered (check console logs for "[L3] Generating feedback...")
- [ ] Receive L3 summary in Telegram with:
  - WHAT WE KNOW section
  - BIGGEST OPEN RISK section
  - DECISION QUESTION with Yes/Adjust/Kill options
- [ ] Verify session state is AWAITING_APPROVAL
- [ ] Verify no 4th question is asked

---

## Benefits

### 1. Prevents Question Spam
- Hard limit of 3 questions per session
- No more endless clarification loops

### 2. Improves Question Quality
- Gemini knows which question number it's on (1/3, 2/3, 3/3)
- Prompt explicitly tells it to make each question count
- Warns about remaining questions to prioritize critical info

### 3. Auto L3 Trigger
- No manual intervention needed
- Seamless transition from clarification to decision
- CEO gets summary automatically after answering 3 questions

### 4. Better UX
- CEO knows progress (question X of 3)
- Clear end point to clarification phase
- Predictable flow: 3 questions → decision

---

## Files Modified

1. **agents/l1_clarity_agent.py**
   - Added question counter
   - Updated prompt with question number
   - Added early return when 3 questions reached
   - Added `clarification_complete` field to return value

2. **main.py**
   - Added check for `clarification_complete`
   - Auto-trigger L3 when clarification done
   - Send L3 feedback to CEO automatically

3. **Database**
   - Reset sessions to NEEDS_CLARIFICATION

---

## Code Quality

- ✅ Clear logging for question progress (X/3)
- ✅ No breaking changes to existing functionality
- ✅ Early return prevents unnecessary API calls to Gemini
- ✅ Maintains backward compatibility (adds new field, doesn't remove old ones)
- ✅ Proper error handling in L3 trigger

---

## Next Steps for Testing

1. **Start the system**:
   ```bash
   python3 main.py
   ```

2. **Open Telegram** and send:
   ```
   I want to expand my company internationally
   ```

3. **Watch console** for:
   - `[L1] Questions asked so far: 0/3`
   - `[L1] Questions asked so far: 1/3`
   - `[L1] Questions asked so far: 2/3`
   - `[L1] Questions asked so far: 3/3`
   - `[L1] ✓ Maximum questions reached (3/3)`
   - `[L3] Generating feedback...`

4. **Check Telegram** for:
   - 3 questions (no more)
   - L3 summary after 3rd answer
   - DECISION QUESTION with Yes/Adjust/Kill

---

**Status**: ✅ Implementation Complete  
**Tested**: ⏳ Pending end-to-end test via Telegram  
**Breaking Changes**: ❌ None  
**Ready for**: ✅ Testing

---

## Related Documentation

- `MAIN_PIPELINE_GUIDE.md` - Pipeline architecture
- `MAIN_PIPELINE_FIXES.md` - Previous bug fixes
- `L1_IMPLEMENTATION_SUMMARY.md` - L1 agent details
- `L3_IMPLEMENTATION_SUMMARY.md` - L3 agent details
