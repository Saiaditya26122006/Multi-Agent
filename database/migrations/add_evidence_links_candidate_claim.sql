-- Migration: Add the missing evidence_links.candidate_claim column
-- Created: 2026-07-17
--
-- Why this exists:
-- add_evidence_links.sql creates the table with `CREATE TABLE IF NOT EXISTS`.
-- The table was created before candidate_claim was added to that file, so every
-- later re-run was a no-op and the column was never created. The live table has
-- 13 of the 14 columns that migration declares; candidate_claim is the gap.
--
-- This was previously read as a Supabase schema-cache delay ("wait ~5 minutes").
-- It is not — the DDL never ran. services/evidence_links.py and
-- scripts/bp11_pilot_setup.py both write candidate_claim, so the BP.1.1 pilot
-- fails until this is applied.

ALTER TABLE evidence_links ADD COLUMN IF NOT EXISTS candidate_claim TEXT;

COMMENT ON COLUMN evidence_links.candidate_claim IS 'The specific atomic claim this link assesses (item → candidate_claim → node). Required for final sufficiency assessment.';
