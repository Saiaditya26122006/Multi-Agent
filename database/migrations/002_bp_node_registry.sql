-- ========================================
-- MIGRATION 002: BP Node Registry Linking
-- ========================================
-- Run this in Supabase SQL Editor
-- Links knowledge_base entries to their business_plan_sections nodes

-- Add bp_section_id column to knowledge_base
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS
  bp_section_id UUID REFERENCES business_plan_sections(id);

-- Create indexes for BP node lookups
CREATE INDEX IF NOT EXISTS idx_kb_bp_section ON knowledge_base(bp_section_id);
CREATE INDEX IF NOT EXISTS idx_kb_section_match ON knowledge_base(section);
CREATE INDEX IF NOT EXISTS idx_bps_section_id ON business_plan_sections(section_id);

-- VERIFICATION QUERIES (run these to confirm migration worked)
--
-- SELECT column_name FROM information_schema.columns
-- WHERE table_name='knowledge_base'
-- AND column_name='bp_section_id';
-- Expected: bp_section_id (1 row)
--
-- SELECT COUNT(*) FROM pg_indexes
-- WHERE tablename='knowledge_base'
-- AND indexname IN ('idx_kb_bp_section', 'idx_kb_section_match');
-- Expected: 2 (indexes created)
