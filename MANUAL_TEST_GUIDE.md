# Manual Testing Guide - L1 Question Limit Fix

## Quick Test (5 minutes)

### Prerequisites
- ✅ Gemini API key updated (new key in `.env`)
- ✅ Supabase database accessible
- ✅ Telegram bot configured
- ✅ You have access to Telegram app

---

## Step-by-Step Test

### 1. Start the System

```bash
cd /home/saiaditya26122006/multi-agent-system
python3 main.py
```

**Expected output**:
```
============================================================
    MULTI-AGENT AI SYSTEM
    CEO Business Planning Assistant
============================================================
Started at: 2026-05-14 XX:XX:XX
...
[STARTUP] System status: ✅ READY
[SYSTEM] Starting Telegram polling...
[SYSTEM] Waiting for messages...
```

Keep this terminal open to watch the logs.

---

### 2. Open Telegram

Go to your Telegram bot and send this message:

```
I want to grow my business
```

---

### 3. Answer Question 1/3

**You should receive**: A clarifying question from the bot

**Console should show**:
```
[L1] Questions asked so far: 0/3
[L1] ✅ Clarifying question generated successfully (1/3)
```

**In Telegram**: Answer the question with any response, e.g.:
```
I want to increase revenue
```

---

### 4. Answer Question 2/3

**You should receive**: Another clarifying question

**Console should show**:
```
[L1] Questions asked so far: 1/3
[L1] ✅ Clarifying question generated successfully (2/3)
```

**In Telegram**: Answer the question, e.g.:
```
By targeting new markets
```

---

### 5. Answer Question 3/3

**You should receive**: One more clarifying question

**Console should show**:
```
[L1] Questions asked so far: 2/3
[L1] ✅ Clarifying question generated successfully (3/3)
```

**In Telegram**: Answer the question, e.g.:
```
Within the next 6 months
```

---

### 6. ✨ CRITICAL: L3 Auto-Trigger

**What should happen**:
- ❌ You should **NOT** receive a 4th question
- ✅ You should receive L3 feedback summary instead

**Console should show**:
```
[L1] Questions asked so far: 3/3
[L1] ✓ Maximum questions reached (3/3)
[L1] ✓ Clarification phase complete, ready for L3
[L1] ✓ Triggering L3 Feedback Agent...

[L3] Generating feedback based on clarification...
[L3] ✓ Loaded CEO context: Alex Zamurko
[L3] ✓ Loaded 3 assumptions
[L3] ✓ Generating feedback with Gemini...
[L3] ✓ Feedback generated
[L3] ✓ Created decision: decision_20260514_XXXXXX
[L3] ✓ Session state updated to AWAITING_APPROVAL
[L3] ✓ Feedback sent to CEO
[L3] Decision ID: decision_20260514_XXXXXX

[PIPELINE] ✓ L1 → L3 transition complete
```

**In Telegram**, you should receive a message like:

```
WHAT WE KNOW
[Summary paragraph about what the system learned from your 3 answers]

BIGGEST OPEN RISK
[One sentence about the biggest uncertainty]

DECISION QUESTION
Should we proceed with [specific approach based on your answers]?
• Yes - proceed as planned
• Adjust - modify the approach
• Kill - stop this initiative
```

---

## Success Criteria

### ✅ All tests pass if:

1. **Exactly 3 questions asked** (not 4, not 2, exactly 3)
2. **Console logs show**:
   - `[L1] Questions asked so far: 0/3`
   - `[L1] Questions asked so far: 1/3`
   - `[L1] Questions asked so far: 2/3`
   - `[L1] Questions asked so far: 3/3`
   - `[L1] ✓ Maximum questions reached (3/3)`
   - `[L1] ✓ Triggering L3 Feedback Agent...`
   - `[L3] Generating feedback...`
   - `[L3] ✓ Feedback sent to CEO`

3. **Telegram message has all sections**:
   - WHAT WE KNOW
   - BIGGEST OPEN RISK
   - DECISION QUESTION with Yes/Adjust/Kill

4. **No 4th question** is asked

---

## If Something Goes Wrong

### Problem: Bot doesn't respond

**Solution**:
1. Check if `main.py` is still running
2. Check console for error messages
3. Restart with `python3 main.py`

### Problem: More than 3 questions asked

**Solution**:
1. This means the fix didn't work
2. Check if you're using the updated code
3. Run: `git status` to see if files were modified
4. Share console logs for debugging

### Problem: No L3 feedback after question 3

**Solution**:
1. Check console logs for L3 trigger
2. Look for error messages about Gemini API
3. Verify API key in `.env` is correct
4. Check if you're over API quota

### Problem: L3 feedback missing sections

**Solution**:
1. This is an L3 issue, not L1
2. Check Gemini API response in logs
3. May need to adjust L3 prompt

---

## Quick Verification Checklist

After testing, verify:

- [ ] Received exactly 3 questions (no more, no less)
- [ ] Console showed question counter (0/3, 1/3, 2/3, 3/3)
- [ ] Console showed "Maximum questions reached (3/3)"
- [ ] Console showed "Triggering L3 Feedback Agent"
- [ ] Received L3 feedback in Telegram
- [ ] Feedback had WHAT WE KNOW section
- [ ] Feedback had BIGGEST OPEN RISK section
- [ ] Feedback had DECISION QUESTION with Yes/Adjust/Kill
- [ ] No 4th question was asked

---

## Alternative: Reset and Test Again

If you want to test multiple times:

**Option 1**: Send `/reset` in Telegram
```
/reset
```

**Option 2**: Run cleanup script
```bash
python3 -c "
from memory.supabase_client import supabase
supabase.table('sessions').update({'state': 'NEEDS_CLARIFICATION'}).neq('state', 'COMPLETED').execute()
print('Sessions reset')
"
```

Then start from step 2 again.

---

## Expected Time

- **Setup**: 1 minute (start main.py)
- **Testing**: 3-4 minutes (answer 3 questions)
- **Verification**: 1 minute (check L3 feedback)
- **Total**: ~5 minutes

---

## What to Report

After testing, report:

1. ✅ **PASS** if all success criteria met
2. ❌ **FAIL** if any criteria not met (include console logs and screenshot)

---

**Ready to test?** Start with step 1! 🚀
