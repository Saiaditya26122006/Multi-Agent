# Database Migration Guide

## Adding Web Interface_chat_id to ceo_context

The L0 Input Guard agent requires the `Web Interface_chat_id` field in the `ceo_context` table to validate message senders.

### Step 1: Run Migration SQL

Go to your Supabase Dashboard SQL Editor and run:

```sql
-- Add Web Interface_chat_id column to ceo_context
ALTER TABLE ceo_context
ADD COLUMN IF NOT EXISTS Web Interface_chat_id BIGINT;

-- Add index for performance
CREATE INDEX IF NOT EXISTS idx_ceo_context_Web Interface_chat_id
ON ceo_context(Web Interface_chat_id);
```

### Step 2: Update CEO Record

Find your CEO's UUID:

```sql
SELECT id, name, company FROM ceo_context;
```

Then update with the CEO's Web Interface chat ID:

```sql
UPDATE ceo_context
SET Web Interface_chat_id = 8866294087  -- Replace with actual CEO's chat ID
WHERE id = 'YOUR-CEO-UUID-HERE';
```

### Step 3: Verify Migration

```sql
SELECT id, name, Web Interface_chat_id FROM ceo_context;
```

You should see the `Web Interface_chat_id` populated.

### Step 4: Test

Run the L0 tests:

```bash
python3 tests/test_l0.py
```

All 3 tests should pass.

## Development Mode

If `Web Interface_chat_id` is NULL in the database, the L0 agent runs in **development mode**:
- ✅ All messages are accepted (no sender validation)
- ⚠️ Warning logged for each message
- 🔧 Useful for local testing

## Production Mode

Once `Web Interface_chat_id` is set:
- ✅ Only messages from CEO's chat ID are accepted
- ❌ Unauthorized senders are rejected with clear error message
- 🔒 Full security validation enabled

## Current CEO Context

As of this migration:
- **CEO**: Alex Zamurko
- **UUID**: b21ddf08-cd2e-4dec-a498-d4f0b4683a43
- **Test Chat ID**: 8866294087
