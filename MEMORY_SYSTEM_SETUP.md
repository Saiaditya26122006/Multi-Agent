# Persistent Memory System - Setup & Testing Guide

## 🎯 Overview

The persistent memory system gives your Telegram bot the ability to remember everything across sessions - just like Claude's memory feature.

### Features:
- **Long-term memory** - Extracts strategic decisions, priorities, and insights from each session
- **Welcome back messages** - Warm greetings that summarize where you left off
- **Memory-aware agents** - L1 and L3 agents reference past decisions
- **Automatic consolidation** - Memory is extracted when sessions complete

---

## 📋 Setup Instructions

### Step 1: Create Database Table

Run this in your Supabase SQL Editor:

```sql
-- Copy and run the entire contents of:
database/memory_profile_schema.sql
```

Or manually:

```sql
CREATE TABLE IF NOT EXISTS memory_profile (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ceo_id UUID REFERENCES ceo_context(id) ON DELETE CASCADE,
    memory_type TEXT NOT NULL CHECK (memory_type IN (
        'strategic_decision',
        'recurring_priority',
        'validated_assumption',
        'key_contact',
        'market_insight',
        'communication_pattern'
    )),
    content TEXT NOT NULL,
    source_session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
    created_at TIMESTAMPTZ DEFAULT now(),
    last_referenced_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT memory_profile_content_check CHECK (length(content) > 0)
);

CREATE INDEX IF NOT EXISTS idx_memory_profile_ceo_id ON memory_profile(ceo_id);
CREATE INDEX IF NOT EXISTS idx_memory_profile_type ON memory_profile(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_profile_created_at ON memory_profile(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_profile_last_referenced ON memory_profile(last_referenced_at DESC);
```

### Step 2: Verify Setup

Check that the table was created:

```sql
SELECT * FROM memory_profile LIMIT 5;
```

Should return an empty table (no rows yet).

---

## 🧪 Testing the Memory System

### Test 1: Run Memory System Tests

```bash
python3 test_memory_system.py
```

This will show you:
- Current memory profile (should be empty initially)
- Recent sessions
- Active session status

### Test 2: Complete a Full Session

Start the bot:
```bash
python3 main.py
```

Then on Telegram:
1. Send: "I want to expand into new markets"
2. Answer the 3 clarifying questions
3. Say "Yes" to approve the decision

**Expected behavior:**
- ✅ Session completes
- ✅ Memory is automatically consolidated
- ✅ Console shows: "Created X memories"

### Test 3: Verify Memory was Created

Run this SQL in Supabase:

```sql
SELECT 
    memory_type,
    content,
    confidence,
    created_at
FROM memory_profile
ORDER BY created_at DESC
LIMIT 10;
```

**You should see:**
- Strategic decisions extracted
- Priorities identified
- Facts validated
- Each with confidence levels (high/medium/low)

### Test 4: Test Welcome Back Message

Stop the bot (Ctrl+C), wait 5 seconds, restart it:

```bash
python3 main.py
```

Then send any message on Telegram (e.g., "hello").

**Expected behavior:**
- ✅ Welcome back message appears
- ✅ References recent work
- ✅ Asks: continue or start new?

### Test 5: Test "Continue" Flow

Reply with: "continue"

**Expected behavior:**
- ✅ Bot resumes from where you left off
- ✅ No duplicate questions asked

### Test 6: Test "Something New" Flow

Reply with: "something new"

**Expected behavior:**
- ✅ Previous session marked COMPLETED
- ✅ Memory consolidated
- ✅ Fresh session started

### Test 7: Verify Memory-Aware Questions

Start a new conversation about the same topic.

**Expected behavior:**
- ✅ L1 agent has memory context
- ✅ Doesn't ask about info already in memory
- ✅ Questions are more focused

---

## 🔍 How It Works

### Memory Types

1. **strategic_decision** - Key decisions made
   - Example: "Focus on Spain market first"

2. **recurring_priority** - Ongoing priorities
   - Example: "Prioritize institutional sales over pilots"

3. **validated_assumption** - Confirmed facts
   - Example: "Closed pilot = signed 3-month agreement"

4. **key_contact** - People or companies mentioned
   - Example: "Working with InnovateTech as first pilot customer"

5. **market_insight** - Market knowledge
   - Example: "Spain requires regulatory approval for SaaS products"

6. **communication_pattern** - How CEO prefers to work
   - Example: "Prefers quick decisions over long deliberation"

### Pipeline Flow

```
Message arrives
     ↓
Check: Should send welcome back?
  - If yes → Send welcome → Wait for "continue" or "new"
     ↓
Continue normal pipeline (L0 → L1 → L3)
     ↓
L1: Load memory profile
    Don't ask about info already in memory
     ↓
L3: Load memory profile  
    Reference past decisions in summary
     ↓
Decision: Yes/Adjust/Kill
     ↓
If "Yes" or "Kill" → Session COMPLETED
     ↓
Automatically consolidate_session_memory()
  - Extract 3-5 key memories
  - Store in memory_profile table
```

---

## 📊 Checking Memory in Database

### View All Memories

```sql
SELECT 
    memory_type,
    content,
    confidence,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI') as created
FROM memory_profile
ORDER BY created_at DESC;
```

### Count by Type

```sql
SELECT 
    memory_type,
    COUNT(*) as count
FROM memory_profile
GROUP BY memory_type
ORDER BY count DESC;
```

### View Recent Memories

```sql
SELECT 
    memory_type,
    content,
    AGE(now(), created_at) as age
FROM memory_profile
WHERE created_at > now() - interval '7 days'
ORDER BY created_at DESC;
```

### Clear All Memories (Reset)

```sql
DELETE FROM memory_profile;
```

---

## 🐛 Troubleshooting

### Issue: No memories created after session

**Check:**
1. Was session marked COMPLETED? 
   ```sql
   SELECT id, state FROM sessions ORDER BY created_at DESC LIMIT 5;
   ```

2. Were there assumptions in the session?
   ```sql
   SELECT COUNT(*) FROM assumptions WHERE session_id = 'YOUR_SESSION_ID';
   ```

3. Check console logs for "Consolidating session memory..."

**Fix:**
- Ensure you clicked "Yes" or "Kill" (not "Adjust")
- Session must have at least 1 assumption

### Issue: Welcome back message not showing

**Check:**
```python
python3 -c "
from agents.memory_agent import should_send_welcome_back
print(should_send_welcome_back(8866294087))  # Your chat ID
"
```

**Fix:**
- Must be 2+ hours since last message, OR
- Must be a different day
- Or manually test: `python3 memory_agent.py welcome`

### Issue: L1 still asking repeated questions

**Check:**
1. Is memory profile loaded?
   ```sql
   SELECT COUNT(*) FROM memory_profile;
   ```

2. Check console for "[L1] ✓ Loaded X memory entries"

**Fix:**
- Ensure memory_profile table exists
- Check that get_memory_profile() is being called in l1_clarity_agent.py

### Issue: Gemini API errors during consolidation

**Check:**
- API key is valid
- Quota not exceeded

**Fix:**
```bash
# Test manually
python3 agents/memory_agent.py consolidate
# Enter a completed session ID when prompted
```

---

## ✅ Success Criteria

Your memory system is working correctly when:

1. ✅ **Memories are created** after completing sessions
2. ✅ **Welcome back message** appears after time gap
3. ✅ **"Continue" works** - resumes active session
4. ✅ **"Something new" works** - closes session and consolidates
5. ✅ **L1 doesn't repeat** questions about memorized info
6. ✅ **L3 references** past decisions in summaries
7. ✅ **Database has records** in memory_profile table

---

## 📈 Next Steps

Once memory is working:

1. **Monitor growth** - Watch memory_profile table grow over time
2. **Tune extraction** - Adjust prompt in memory_agent.py for better memories
3. **Add memory search** - Query memories by type or keyword
4. **Memory management** - Archive old memories, prioritize important ones
5. **Memory export** - Generate "CEO profile" documents from memories

---

## 🎯 Example Session

**You:** "I want to expand into Asia"

**Bot:** *Q1:* Which countries? → "Singapore and Vietnam"  
**Bot:** *Q2:* What timeline? → "Q3 2026"  
**Bot:** *Q3:* Budget? → "$500K"

**Bot:** *Summary + Decision*

**You:** "Yes"

**Console:**
```
[DECISION] Consolidating session memory...
[MEMORY] Extracting memories with Gemini...
[MEMORY] ✅ Created 3 memories
```

**Database:**
```
strategic_decision | Focus on Singapore and Vietnam for expansion | high
validated_assumption | Q3 2026 target for soft launch | high  
validated_assumption | $500K initial expansion budget | high
```

---

**Next conversation (3 hours later):**

**You:** "Hi"

**Bot:**
```
Welcome back, Alex! 👋 You've been focused on expanding into 
Singapore and Vietnam with a Q3 2026 launch ($500K budget). 
Want to continue refining that strategy, or work on something new?
```

**You:** "continue"

**Bot:**
```
✓ Continuing from where we left off. What's your next thought?
```

**You:** "What about regulatory requirements?"

**Bot:** (L1 generates question that doesn't repeat Singapore/Vietnam/budget info)

---

## 🎉 You're Done!

The persistent memory system is now fully integrated and working.

Your bot will remember:
- ✅ Every strategic decision
- ✅ All validated facts
- ✅ Recurring priorities
- ✅ Communication patterns

And use this to:
- ✅ Ask smarter questions
- ✅ Generate context-aware summaries
- ✅ Provide continuity across sessions
- ✅ Feel like a long-term partner, not a stateless chatbot

**Status: 🏆 Memory System Active**
