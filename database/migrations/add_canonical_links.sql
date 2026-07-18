-- Migration: Canonical-entity linkage (Alex audit issue #1 — two overlapping systems)
-- Created: 2026-07-18
-- Purpose: assumptions/decisions live BOTH in operational tables (assumptions,
-- decisions) AND as knowledge_base rows (source_type assumption_lifecycle /
-- decision), duplicating truth. Decision: the OPERATIONAL tables are CANONICAL;
-- knowledge_base rows are INDEXED REPRESENTATIONS for retrieval. This adds an
-- explicit pointer from each representation row back to its canonical row so the
-- two systems are reconciled rather than divergent.
--
-- APPLY ONCE via the Supabase SQL Editor. This is the linkage foundation; the
-- full data backfill/dedup is a separate migration once every writer sets it.

ALTER TABLE knowledge_base
    ADD COLUMN IF NOT EXISTS canonical_table TEXT,   -- 'assumptions' | 'decisions' | NULL
    ADD COLUMN IF NOT EXISTS canonical_id UUID;      -- FK-by-convention to that table's id

CREATE INDEX IF NOT EXISTS idx_kb_canonical ON knowledge_base(canonical_table, canonical_id);

COMMENT ON COLUMN knowledge_base.canonical_table IS 'Canonical source table for this row when it is an indexed representation (assumptions/decisions). NULL = knowledge_base is itself canonical (ceo_doc, conversation, etc.).';
COMMENT ON COLUMN knowledge_base.canonical_id IS 'Row id in canonical_table this representation mirrors. Reconciles Alex audit issue #1 (two overlapping systems).';
