-- Migration: feed_corrections — durable store of Alex's Feed review decisions
-- Created: 2026-07-23
-- Purpose: Every confirm/adjust in Feed review is a labeled example (what the
-- classifier suggested vs what Alex accepted). This was written to an ephemeral
-- JSONL file on the container FS (wiped on every Railway redeploy), so the
-- classifier never accumulated any training signal. Persist it here instead.

CREATE TABLE IF NOT EXISTS feed_corrections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    original_node_id text,
    corrected_node_id text,
    fact_content text,
    correction_type text,          -- 'confirmed' | 'corrected'
    session_id text,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feed_corrections_created ON feed_corrections(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_corrections_original_node ON feed_corrections(original_node_id);

COMMENT ON TABLE feed_corrections IS 'Labeled examples from Alex''s Feed review (confirm/adjust). Few-shot training signal for the classifier.';
