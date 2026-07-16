-- Fix RLS on system tables for Phase 2 knowledge graph population
-- These tables contain framework state, not user data

ALTER TABLE chunk_relationships DISABLE ROW LEVEL SECURITY;
ALTER TABLE bp12_register DISABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_links DISABLE ROW LEVEL SECURITY;

-- Verify
SELECT schemaname, tablename, rowsecurity FROM pg_tables
WHERE tablename IN ('chunk_relationships', 'bp12_register', 'evidence_links')
ORDER BY tablename;
