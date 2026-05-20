# 🚀 Memory System Quick Start

## ✅ 5-Minute Setup

### Step 1: Create Database Table (2 min)

Open Supabase SQL Editor and run:

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

CREATE INDEX idx_memory_profile_ceo_id ON memory_profile(ceo_id);
CREATE INDEX idx_memory_profile_type ON memory_profile(memory_type);
CREATE INDEX idx_memory_profile_created_at ON memory_profile(created_at DESC);
```

✅ Done? → Continue

### Step 2: Test the System (1 min)

```bash
python3 test_memory_system.py
```

Expected: 4/5 tests pass (consolidation test requires a session)

✅ Tests passed? → Continue

### Step 3: Complete a Full Session (2 min)

```bash
python3 main.py
```

On Telegram:
1. Send: "I want to expand my business"
2. Answer 3 questions
3. Click "Yes"

Console should show:
```
[DECISION] Consolidating session memory...
[MEMORY] ✅ Created 3 memories
```

✅ Memories created? → Continue

### Step 4: Verify in Database (<1 min)

```sql
SELECT memory_type, content FROM memory_profile;
```

Expected: 3-5 memory entries

✅ Memories in database? → **ALL DONE!** 🎉

---

## 🧪 Test Welcome Back

1. Stop the bot (Ctrl+C)
2. Wait 5 seconds
3. Restart: `python3 main.py`
4. Send any message on Telegram

Expected:
```
Welcome back, Alex! 👋 You've been focused on...
```

---

## 🎯 Quick Reference

| Command | What it does |
|---------|-------------|
| `python3 main.py` | Start bot (memory active) |
| `python3 test_memory_system.py` | Run tests |
| Reply "continue" | Resume last session |
| Reply "something new" | Close + start fresh |

---

## ✅ Success Checklist

- [ ] Database table created
- [ ] Tests run (4/5 passing)
- [ ] Completed one session
- [ ] Memories in database
- [ ] Welcome back works
- [ ] Continue/new works

All checked? **You're done!** 🚀

---

## 📚 Full Documentation

- **Setup Guide:** MEMORY_SYSTEM_SETUP.md
- **Complete Docs:** MEMORY_SYSTEM_COMPLETE.md
- **Database Schema:** database/memory_profile_schema.sql

---

**Time to Complete:** ~5 minutes  
**Difficulty:** Easy  
**Status:** Ready to use!
