# Database Setup Instructions

## Issue: Row Level Security (RLS)

The tests are failing because Supabase has Row Level Security enabled by default. When using the `SUPABASE_ANON_KEY`, RLS policies block all operations unless explicitly allowed.

## Solution: Apply Database Scripts

You need to run these SQL scripts in your Supabase SQL Editor (in order):

### Step 1: Create the Schema
Run `schema.sql` in the Supabase SQL Editor to create all tables.

### Step 2: Disable RLS for Development
Run `setup_rls.sql` to disable RLS on all tables. This allows the anon key to work.

**⚠️ Important:** In production, you should use proper RLS policies instead of disabling RLS entirely. The `setup_rls.sql` file contains commented code for setting up permissive policies.

### Step 3: Seed Test Data
Run `seed_test_data.sql` to insert initial CEO context data for testing.

## How to Run SQL Scripts in Supabase

1. Go to your Supabase project dashboard: https://supabase.com/dashboard
2. Navigate to the SQL Editor (left sidebar)
3. Create a new query
4. Copy and paste the contents of each SQL file
5. Click "Run" or press Ctrl+Enter

## Verify Setup

After running all scripts, run the test:

```bash
python tests/test_supabase.py
```

All 7 tests should pass.

## Alternative: Use Service Role Key

Instead of disabling RLS, you can use the service role key which bypasses RLS:

1. Get your service role key from Supabase Dashboard → Settings → API
2. Add it to your `.env` file as `SUPABASE_SERVICE_KEY`
3. Update `memory/supabase_client.py` to use the service key instead of anon key

**⚠️ Warning:** Never expose the service role key in client-side code or commit it to public repositories.
