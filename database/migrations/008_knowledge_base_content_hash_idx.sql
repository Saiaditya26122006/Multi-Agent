-- Migration: index the dedup lookup on knowledge_base
-- Created: 2026-07-28
-- Requires: knowledge_base (add_knowledge_base.sql)
--
-- WHY
--
-- rag_service.store() checks for an existing row by content hash before every
-- insert:
--
--     .eq("metadata->>content_hash", h)
--
-- Nine indexes exist on knowledge_base and none of them covers that expression,
-- so the check is a sequential scan over the JSONB column on every single write.
-- Measured at 1,944 rows: 2.84s per lookup. A real Feed document produces ~170
-- facts, so the dedup check alone cost ~8 minutes of a 28-minute run, and it
-- gets worse linearly as the knowledge base grows — which is the one thing this
-- table is designed to do.
--
-- The expression here must match the query exactly. PostgREST's
-- `metadata->>content_hash` compiles to `(metadata ->> 'content_hash')`; an
-- index on `metadata` (jsonb_path_ops) would NOT be used by it.

CREATE INDEX IF NOT EXISTS knowledge_base_content_hash_idx
    ON knowledge_base ((metadata ->> 'content_hash'));

-- Verify it is actually used (expect Index Scan, not Seq Scan):
--
--   EXPLAIN ANALYZE
--   SELECT id FROM knowledge_base
--   WHERE metadata ->> 'content_hash' = 'any-64-hex-string'
--   LIMIT 1;
