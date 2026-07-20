# Redis to Session State Migration Guide

## Status: Infrastructure Complete ✅

### What's Been Implemented

✅ **Custom Message Bus** (`services/message_bus.py`)
- In-memory asyncio.Queue for agent-to-agent communication
- Message tracing, ACK, timeout, and retry handling
- Dead letter queue for failed messages
- Zero external dependencies

✅ **Session State Storage** (`Supabase sessions table`)
- Added 7 new columns for temporary state:
  - `awaiting_clarification` (BOOLEAN)
  - `awaiting_approval` (BOOLEAN)
  - `gate2_active` (BOOLEAN)
  - `last_question` (TEXT)
  - `challenge_pending_task_id` (TEXT)
  - `adjust_pending_task_id` (TEXT)
  - `clarification_data` (JSONB)

✅ **Session State Helpers** (`memory/supabase_client.py`)
- `set_session_flag(session_id, flag_name, value)`
- `get_session_flag(session_id, flag_name, default)`
- `set_session_data(session_id, data_key, value)`
- `get_session_data(session_id, data_key, default)`
- `clear_session_flags(session_id)`

✅ **Session State Manager** (`tools/session_state.py`)
- Async wrapper class for easy usage in main.py
- Methods for all session state types

---

## What Still Needs Done

### 1. Update main.py (handle_message function)

**Current Status:** 23 Redis calls still in place  
**Target:** Zero Redis calls

#### Replace Pattern 1: Topic Change Notification
```python
# BEFORE (Redis)
topic_notify_key = f"topic_change_notify:{chat_id}"
old_idea = safe_redis_get(topic_notify_key)
if old_idea:
    # ... handle notification
    safe_redis_delete(topic_notify_key)

# AFTER (Session State)
# Remove this block - topic change is detected by get_active_session()
# with message_text parameter already. No Redis needed.
```

#### Replace Pattern 2: Challenge Pending
```python
# BEFORE (Redis)
challenge_key = f"challenge_pending:{chat_id}"
challenge_task_id = safe_redis_get(challenge_key)
if challenge_task_id:
    # ... handle challenge
    safe_redis_delete(challenge_key)

# AFTER (Session State)
challenge_task_id = await session_state.get_challenge_pending(session_id)
if challenge_task_id:
    # ... handle challenge
    await session_state.clear_challenge_pending(session_id)
```

#### Replace Pattern 3: Adjust Pending
```python
# BEFORE (Redis)
adjust_key = f"adjust_pending:{chat_id}"
adjust_task_id = safe_redis_get(adjust_key)
if adjust_task_id:
    # ... handle adjust
    safe_redis_delete(adjust_key)

# AFTER (Session State)
adjust_task_id = await session_state.get_adjust_pending(session_id)
if adjust_task_id:
    # ... handle adjust
    await session_state.clear_adjust_pending(session_id)
```

#### Replace Pattern 4: Gate2 Active
```python
# BEFORE (Redis)
gate2_active = safe_redis_get(f"gate2_active_group:{chat_id}")
if gate2_active:
    # Gate 2 listener will handle this
    return

# AFTER (Session State)
if await session_state.is_gate2_active(session_id):
    # Gate 2 listener will handle this
    return
```

#### Replace Pattern 5: Last Question
```python
# BEFORE (Redis)
last_q_key = f"last_question:{chat_id}"
last_question_raw = safe_redis_get(last_q_key)
# ...later...
safe_redis_set(last_q_key, question, ex=86400)
safe_redis_delete(last_q_key)

# AFTER (Session State)
last_question = await session_state.get_last_question(session_id)
# ...later...
await session_state.set_last_question(session_id, question)
# No need to delete - just set to None or empty
```

#### Replace Pattern 6: Awaiting Clarification
```python
# BEFORE (Redis)
clarification_key = f"awaiting_clarification:{chat_id}"
clarification_data = safe_redis_get(clarification_key)
if clarification_data:
    # ... handle clarification
    safe_redis_delete(clarification_key)

# AFTER (Session State)
clarification_data = await session_state.get_clarification_data(session_id)
if clarification_data:
    # ... handle clarification
    await session_state.clear_clarification_data(session_id)
```

#### Replace Pattern 7: Welcome Sent Flag
```python
# BEFORE (Redis)
welcome_key = f"welcome_sent:{chat_id}"
welcome_already_sent = safe_redis_get(welcome_key) is not None
# ...later...
safe_redis_set(welcome_key, "1", ex=7200)

# AFTER (Session State)
# Skip this - it's implicit in active_session check
# Just omit the welcome_already_sent check

# OR if you want to be more explicit, add a flag to sessions table
# and use it here
```

#### Replace Pattern 8: Proceed/Skip Responses
```python
# BEFORE (Redis)
safe_redis_set(
    f"proceed_response:{proceed_session_id}",
    text_lower,
    ex=7200,
)

# AFTER (Session State)
# This is Pipeline trigger state - can be stored as:
await set_session_data(session_id, "proceed_response", text_lower)
```

### 2. Remove Redis Client Initialization

```python
# DELETE from main.py
from memory.redis_client import redis_client

# The redis_client import is already removed, but if it appears elsewhere
# in your codebase (tests, services), remove it too.
```

### 3. Update Tests

All tests that use Redis need updating:
- `tests/test_l0.py` - Remove Redis mocks
- `tests/test_l1.py` - Use session state instead
- `tests/test_l3.py` - Use session state instead
- `tests/test_full_pipeline_e2e.py` - Use session state instead

Replace patterns:
```python
# BEFORE (mock Redis)
mock_redis = MagicMock()

# AFTER (just use real session state in tests)
# No mock needed - session state goes to Supabase
```

### 4. Run Database Migration

Before deploying, run the migration on your Supabase database:

```bash
# SSH to your Supabase environment or use Supabase dashboard
# Run this SQL:
psql -U postgres -d your_db -f database/migrations/003_add_session_state_columns.sql

# Or paste into Supabase SQL Editor:
# (contents of 003_add_session_state_columns.sql)
```

### 5. Deployment Checklist

- [ ] All safe_redis_* calls removed from main.py
- [ ] All Redis imports removed
- [ ] All tests updated to use session state
- [ ] Database migration applied to Supabase
- [ ] .env file no longer references UPSTASH_REDIS_*
- [ ] requirements.txt no longer includes upstash-redis
- [ ] Local Redis not needed for development

---

## Benefits of This Migration

| Aspect | Redis | Session State |
|--------|-------|--------------|
| **Latency** | <1ms | ~50ms (Supabase) |
| **Infrastructure** | Extra service | Already have Supabase |
| **Persistence** | Ephemeral | Persistent across restarts |
| **Queryability** | Not queryable | SQL queries available |
| **Dependencies** | upstash-redis lib | Zero new deps |
| **Debugging** | Hard to inspect | Easy to inspect via DB |
| **Cost** | $7/month | Included in Supabase plan |
| **Complexity** | Managing TTLs | Automatic |

---

## Implementation Steps (Recommended Order)

### Phase 1: Infrastructure (DONE ✅)
- [x] Create message bus
- [x] Add session state columns
- [x] Add Supabase helpers
- [x] Add SessionStateManager

### Phase 2: Main Handler (TODO)
1. Replace pattern-by-pattern in handle_message()
2. Replace pattern-by-pattern in handle_callback()
3. Test each replacement manually
4. Remove safe_redis_* functions

### Phase 3: Testing (TODO)
1. Update L0 tests
2. Update L1 tests
3. Update L3 tests
4. Update E2E tests

### Phase 4: Deployment (TODO)
1. Run database migration
2. Deploy code
3. Monitor for issues
4. Remove upstash-redis from requirements.txt
5. Remove Redis environment variables from .env

---

## Quick Reference: Session State API

```python
from tools.session_state import session_state

# Flags (boolean)
await session_state.set_awaiting_clarification(session_id, True)
is_waiting = await session_state.is_awaiting_clarification(session_id)

await session_state.set_challenge_pending(session_id, task_id)
task_id = await session_state.get_challenge_pending(session_id)
await session_state.clear_challenge_pending(session_id)

await session_state.set_adjust_pending(session_id, task_id)
task_id = await session_state.get_adjust_pending(session_id)
await session_state.clear_adjust_pending(session_id)

await session_state.set_gate2_active(session_id, True)
is_active = await session_state.is_gate2_active(session_id)

# Data fields
await session_state.set_last_question(session_id, "What's your target market?")
q = await session_state.get_last_question(session_id)

await session_state.set_clarification_data(session_id, task_id, run_id)
data = await session_state.get_clarification_data(session_id)
await session_state.clear_clarification_data(session_id)
```

---

## Testing Locally

Before deploying, test the migration locally:

```python
# Test script
import asyncio
from tools.session_state import session_state
from memory.supabase_client import create_session, get_ceo_context

async def test_session_state():
    ceo = get_ceo_context()
    session = create_session(ceo['id'], 'test-chat-123')
    session_id = session['id']
    
    # Test flag
    assert await session_state.set_awaiting_clarification(session_id, True)
    assert await session_state.is_awaiting_clarification(session_id) == True
    
    # Test data
    assert await session_state.set_last_question(session_id, "Test question?")
    assert await session_state.get_last_question(session_id) == "Test question?"
    
    print("✅ All session state tests passed!")

asyncio.run(test_session_state())
```

---

## Troubleshooting

### Session state not persisting?
- Check Supabase migration applied: `\d sessions` should show new columns
- Check your SUPABASE_ANON_KEY has write permissions

### Tests failing after migration?
- Make sure all tests import SessionStateManager
- Ensure test_conftest.py uses admin_db for testing

### Performance concerns?
- Session state latency is ~50ms (Supabase round trip)
- This is fine for human-speed interactions (CEO typing)
- Message bus is still microsecond for agent-to-agent

---

## Success Criteria

Migration is complete when:
- [ ] Zero Redis calls in codebase
- [ ] All tests pass
- [ ] No upstash-redis in requirements.txt
- [ ] No UPSTASH_* env vars needed
- [ ] Session state properly persists across restarts
- [ ] All existing functionality works identically
