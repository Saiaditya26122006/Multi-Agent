# RLS Governance Policy Fix Required

## Issue
Both `chunk_relationships` and `bp12_register` tables are blocking writes with:
```
'new row violates row-level security policy for table "..."'
```

## Root Cause
The tables were created with RLS enabled but no policies defined. The Supabase service role (used by agents) cannot insert because RLS denies all writes.

## Solution

Execute in Supabase SQL Editor (as authenticated admin):

```sql
-- Disable RLS on system tables (no user-private data)
ALTER TABLE chunk_relationships DISABLE ROW LEVEL SECURITY;
ALTER TABLE bp12_register DISABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_links DISABLE ROW LEVEL SECURITY;

-- Verify
SELECT schemaname, tablename, rowsecurity FROM pg_tables 
WHERE tablename IN ('chunk_relationships', 'bp12_register', 'evidence_links');
```

## Why These Are System Tables

| Table | Data | RLS Needed? |
|-------|------|------------|
| `chunk_relationships` | Cross-chunk edges (non-sensitive) | ❌ No |
| `bp12_register` | Governance items (system-internal) | ❌ No |
| `evidence_links` | Per-link boundaries (non-sensitive) | ❌ No |
| `knowledge_base` | User content | ✅ Yes (read-only by design) |

These tables are internal system tables that store framework state, not user data.

## After RLS Disabled

Retry:
```bash
# Relationship linking (populates chunk_relationships)
python -c "from services.memory_index import link_new_chunk; ..."

# Contradiction detection (populates bp12_register)
python -c "from services.bp12_register import create_register_item; ..."
```

## Expected Results

- `chunk_relationships`: ~1000+ rows (cross-chunk edges)
- `bp12_register`: ~20-50 rows (detected contradictions)

---

**Manual step required before proceeding with Phase 2.**
