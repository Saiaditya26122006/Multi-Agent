# Supabase Schema Cache Issue

## Problem
After executing the RLS fix migration, Supabase's schema cache doesn't reflect the `candidate_claim` column in `evidence_links` table.

Error:
```
Could not find the 'candidate_claim' column of 'evidence_links' in the schema cache
```

## Status
- ✅ RLS is disabled on all three tables
- ✅ Tables exist in database
- ⚠️ Supabase schema cache is stale

## Solution

Supabase automatically refreshes the schema cache periodically. Quick fixes:

### Option 1: Wait (Automatic)
Schema cache refreshes every ~5 minutes. Wait and retry in 5 minutes.

### Option 2: Force Refresh
In Supabase dashboard:
1. Go to **API** section
2. Click **"Regenerate"** under **API Keys**
   - This doesn't change keys, but forces cache refresh
3. Retry queries

### Option 3: Direct SQL Query
If you need to verify the table structure exists:
```sql
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'evidence_links'
ORDER BY ordinal_position;
```

Expected columns:
- id (uuid)
- chunk_id (uuid)
- target_node_id (text)
- candidate_claim (text) ← This is the one not showing in cache
- claim_supported (text)
- claim_not_supported (text)
- sufficiency_status (text)
- boundary_reason (text)
- requires_corroboration (boolean)
- controller_approved (boolean)
- controller_decision_id (uuid)
- created_at (timestamp)
- updated_at (timestamp)
- session_id (text)

## Workaround While Cache is Stale

Can still test governance system by:
1. Waiting for cache to refresh (~5 min)
2. Or manually inserting via SQL, then querying via API

## Next Steps

1. Wait 5 minutes or force refresh via API Keys page
2. Retry BP.1.1 pilot script
3. Verify evidence_links inserts work
4. Run controller workflow test

---

**Timeline:** RLS fixed ✅ → Schema cache stale ⏳ → Auto-refresh (ETA 5 min)
